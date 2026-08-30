"""Trading 212 parser."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from pathlib import Path
from typing import ClassVar, Final, NoReturn, TextIO, override

from cgt_calc.const import TICKER_RENAMES, UK_TIMEZONE
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    Isin,
    TransactionSource,
)
from cgt_calc.stock_splits import (
    StockSplitEvent,
    StockSplitTransaction,
    UnresolvedRatio,
    quantity_sign,
    recover_ratio,
)
from cgt_calc.util import approx_equal, approx_equal_scaled

from .base_parsers import BaseDirParser


class Trading212Column(StrEnum):
    """Columns exported in the Trading 212 transaction CSV."""

    ACTION = "Action"
    TIME = "Time"
    TIME_UTC = "Time (UTC)"
    ISIN = "ISIN"
    TICKER = "Ticker"
    NAME = "Name"
    NO_OF_SHARES = "No. of shares"
    PRICE_PER_SHARE = "Price / share"
    CURRENCY_PRICE_PER_SHARE = "Currency (Price / share)"
    EXCHANGE_RATE = "Exchange rate"
    RESULT_GBP = "Result (GBP)"
    RESULT = "Result"
    CURRENCY_RESULT = "Currency (Result)"
    TOTAL_GBP = "Total (GBP)"
    TOTAL = "Total"
    CURRENCY_TOTAL = "Currency (Total)"
    WITHHOLDING_TAX = "Withholding tax"
    CURRENCY_WITHHOLDING_TAX = "Currency (Withholding tax)"
    CHARGE_AMOUNT_GBP = "Charge amount (GBP)"
    CHARGE_AMOUNT = "Charge amount"
    CURRENCY_CHARGE_AMOUNT = "Currency (Charge amount)"
    DEPOSIT_FEE = "Deposit fee"
    CURRENCY_DEPOSIT_FEE = "Currency (Deposit fee)"
    TRANSACTION_FEE_GBP = "Transaction fee (GBP)"
    TRANSACTION_FEE = "Transaction fee"
    FINRA_FEE_GBP = "Finra fee (GBP)"
    FINRA_FEE = "Finra fee"
    STAMP_DUTY_GBP = "Stamp duty (GBP)"
    STAMP_DUTY = "Stamp duty"
    CURRENCY_STAMP_DUTY = "Currency (Stamp duty)"
    STAMP_DUTY_RESERVE_TAX = "Stamp duty reserve tax"
    CURRENCY_STAMP_DUTY_RESERVE_TAX = "Currency (Stamp duty reserve tax)"
    FRENCH_TRANSACTION_TAX = "French transaction tax"
    CURRENCY_FRENCH_TRANSACTION_TAX = "Currency (French transaction tax)"
    NOTES = "Notes"
    TRANSACTION_ID = "ID"
    CURRENCY_CONVERSION_FEE_GBP = "Currency conversion fee (GBP)"
    CURRENCY_CONVERSION_FEE = "Currency conversion fee"
    CURRENCY_CURRENCY_CONVERSION_FEE = "Currency (Currency conversion fee)"
    CURRENCY_CONVERSION_FROM_AMOUNT = "Currency conversion from amount"
    CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT = (
        "Currency (Currency conversion from amount)"
    )
    CURRENCY_CONVERSION_TO_AMOUNT = "Currency conversion to amount"
    CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT = "Currency (Currency conversion to amount)"
    CURRENCY_TRANSACTION_FEE = "Currency (Transaction fee)"
    CURRENCY_FINRA_FEE = "Currency (Finra fee)"
    MERCHANT_CATEGORY = "Merchant category"
    MERCHANT_NAME = "Merchant name"


COLUMN_SET: Final[set[str]] = {column.value for column in Trading212Column}
LOGGER = logging.getLogger(__name__)

# A share reorganisation is exported as two rows: the position before it and
# the position after it, in either order. These are values in the Action
# column, not columns of their own, so they are deliberately not part of
# Trading212Column: adding them there would make _validate_header accept a
# column literally named "Stock split open".
SPLIT_OPEN_ACTION: Final = "Stock split open"
SPLIT_CLOSE_ACTION: Final = "Stock split close"
SPLIT_ACTIONS: Final = frozenset({SPLIT_OPEN_ACTION, SPLIT_CLOSE_ACTION})

# Two rounded account-currency totals, so the only error between them is the
# rounding itself.
TOTAL_ABS_TOLERANCE: Final = Decimal("0.01")

# A value rebuilt from an exported price and exchange rate misses by a
# proportion of itself rather than by a fixed step, so this bound has to be
# relative. Measured across the fourteen rows of seven real reorganisations,
# the worst disagreement between the two halves of a pair is 1.8e-9 of the
# value, which this leaves about fifty times over. A fixed bound near the
# absolute figures those events show (up to 5.9e-8) is fitted to holdings
# worth a few hundred pounds and rejects ordinary ones: at 1.8e-9 a GBP
# 100,000 position already differs by 1.8e-4.
VALUE_REL_TOLERANCE: Final = Decimal("1E-7")

# A reorganisation is two rows: the position before it and the one after.
HALVES_PER_SPLIT: Final = 2

# The two halves are stamped at the same instant or milliseconds apart.
SPLIT_PAIR_MAX_GAP: Final = timedelta(seconds=1)

# How far apart two rows can be and still be shown to each other as the
# reorganisation the reader was looking for. Deliberately wider than
# SPLIT_PAIR_MAX_GAP: a pair that misses the gap has to reach the check that
# names the gap, or it is reported as a half whose partner is missing while
# the partner sits a second away in the same file.
SPLIT_CANDIDATE_WINDOW: Final = timedelta(minutes=1)

# Fields a reorganisation cannot carry. It moves no money and realises no
# gain, so anything here means the row is not the reorganisation it claims to
# be. A currency beside one of these cannot mislead: the amount is zero.
SPLIT_ZERO_COLUMNS: Final[tuple[Trading212Column, ...]] = (
    Trading212Column.RESULT,
    Trading212Column.RESULT_GBP,
    Trading212Column.WITHHOLDING_TAX,
    Trading212Column.CHARGE_AMOUNT,
    Trading212Column.CHARGE_AMOUNT_GBP,
    Trading212Column.DEPOSIT_FEE,
    Trading212Column.TRANSACTION_FEE,
    Trading212Column.TRANSACTION_FEE_GBP,
    Trading212Column.FINRA_FEE,
    Trading212Column.FINRA_FEE_GBP,
    Trading212Column.STAMP_DUTY,
    Trading212Column.STAMP_DUTY_GBP,
    Trading212Column.STAMP_DUTY_RESERVE_TAX,
    Trading212Column.FRENCH_TRANSACTION_TAX,
    Trading212Column.CURRENCY_CONVERSION_FEE,
    Trading212Column.CURRENCY_CONVERSION_FEE_GBP,
    Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT,
    Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT,
)


@dataclass(frozen=True)
class FeeColumn:
    """How to read one group of Trading 212 fee columns.

    Broker fees with a blank currency are ambiguous so they must state
    one (`currency_required`); statutory taxes tolerate a blank currency
    (folded into the transaction currency) since some exports leave it
    empty.
    """

    attribute: str
    gbp_column: Trading212Column | None
    bare_column: Trading212Column
    currency_column: Trading212Column
    currency_required: bool


# French transaction tax counts as stamp duty since both are statutory
# transaction taxes that increase the cost basis.
FEE_COLUMNS: Final[list[FeeColumn]] = [
    FeeColumn(
        attribute="transaction_fee",
        gbp_column=Trading212Column.TRANSACTION_FEE_GBP,
        bare_column=Trading212Column.TRANSACTION_FEE,
        currency_column=Trading212Column.CURRENCY_TRANSACTION_FEE,
        currency_required=True,
    ),
    FeeColumn(
        attribute="finra_fee",
        gbp_column=Trading212Column.FINRA_FEE_GBP,
        bare_column=Trading212Column.FINRA_FEE,
        currency_column=Trading212Column.CURRENCY_FINRA_FEE,
        currency_required=True,
    ),
    FeeColumn(
        attribute="stamp_duty",
        gbp_column=Trading212Column.STAMP_DUTY_GBP,
        bare_column=Trading212Column.STAMP_DUTY,
        currency_column=Trading212Column.CURRENCY_STAMP_DUTY,
        currency_required=False,
    ),
    FeeColumn(
        attribute="stamp_duty",
        gbp_column=None,
        bare_column=Trading212Column.STAMP_DUTY_RESERVE_TAX,
        currency_column=Trading212Column.CURRENCY_STAMP_DUTY_RESERVE_TAX,
        currency_required=False,
    ),
    FeeColumn(
        attribute="stamp_duty",
        gbp_column=None,
        bare_column=Trading212Column.FRENCH_TRANSACTION_TAX,
        currency_column=Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX,
        currency_required=False,
    ),
    FeeColumn(
        attribute="conversion_fee",
        gbp_column=Trading212Column.CURRENCY_CONVERSION_FEE_GBP,
        bare_column=Trading212Column.CURRENCY_CONVERSION_FEE,
        currency_column=Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE,
        currency_required=True,
    ),
]


def decimal_or_none(
    row: dict[Trading212Column, str], column: Trading212Column
) -> Decimal | None:
    """Convert a column value to Decimal.

    Return None when the value is blank, missing, or "Not available".
    """

    value = row.get(column)
    if value is None or value in {"", "Not available"}:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as err:
        raise ValueError(f"Invalid decimal in {column.value}: {value!r}") from err
    # "NaN" and "Infinity" parse as decimals but are not amounts. Refused
    # here, with the row, rather than at the first arithmetic that touches
    # them, which raises a bare decimal exception with no context at all.
    if not parsed.is_finite():
        raise ValueError(f"Invalid decimal in {column.value}: {value!r}")
    return parsed


def datetime_from_str(value: str) -> datetime:
    """Convert a timestamp to an aware UTC datetime.

    Exports have used whole seconds and milliseconds, and the column
    renamed to "Time (UTC)" may spell the zone out. Every export states
    its times in UTC, so a value without a zone is read as UTC too.
    """

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"Invalid timestamp: {value!r}") from err
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert label to ActionType."""
    if label in {
        "Market buy",
        "Limit buy",
        "Stop buy",
        "Stop limit buy",
    }:
        return ActionType.BUY

    if label in {
        "Market sell",
        "Limit sell",
        "Stop sell",
        "Stop limit sell",
    }:
        return ActionType.SELL

    if label in {
        "Card credit",
        "Card debit",
        "Card refund",
        "Deposit",
        "Withdrawal",
    }:
        return ActionType.TRANSFER

    if label in {
        "Dividend (Ordinary)",
        "Dividend (Dividend)",
        "Dividend (Dividends paid by us corporations)",
        "Dividend (Dividend manufactured payment)",
        "Dividend (Property income distribution)",
        "Dividend adjustment",
    }:
        return ActionType.DIVIDEND

    if label in {
        "Interest on cash",
        "Lending interest",
        # Interest distributions from bond and money market funds,
        # taxed as savings income rather than dividends.
        "Dividend (Interest)",
    }:
        return ActionType.INTEREST

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label in SPLIT_ACTIONS:
        # Half of a reorganisation. The two halves are combined into one
        # event once the whole directory has been read; a half that never
        # finds its partner is refused there.
        return ActionType.STOCK_SPLIT

    if label == "Spin off":
        return ActionType.SPIN_OFF

    if label in {
        "Currency conversion",
        "Result adjustment",
        "Spending cashback",
    }:
        return ActionType.ADJUSTMENT

    raise ParsingError(file, f"Unknown action: {label}")


class Trading212Transaction(BrokerTransaction):
    """Represents a single Trading 212 transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: Path) -> None:
        """Create transaction from CSV row."""
        if len(row_raw) != len(header):
            raise UnexpectedColumnCountError(row_raw, len(header), file)

        row: dict[Trading212Column, str] = {
            Trading212Column(column): value
            for column, value in zip(header, row_raw, strict=False)
        }

        # Older exports call the column "Time", newer ones "Time (UTC)".
        time_str = row.get(Trading212Column.TIME) or row.get(Trading212Column.TIME_UTC)
        if not time_str:
            raise ValueError(
                f"Missing {Trading212Column.TIME.value} "
                f"or {Trading212Column.TIME_UTC.value}"
            )
        self.datetime = datetime_from_str(time_str)
        # The instant is kept in UTC for ordering, but the date that drives
        # the tax year and the matching rules is the UK one.
        date = self.datetime.astimezone(UK_TIMEZONE).date()
        self.raw_action = row[Trading212Column.ACTION]
        action = action_from_str(self.raw_action, file)

        symbol = row[Trading212Column.TICKER] or None
        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row[Trading212Column.NAME]

        quantity = decimal_or_none(row, Trading212Column.NO_OF_SHARES)
        self.price_foreign = decimal_or_none(row, Trading212Column.PRICE_PER_SHARE)
        self.currency_foreign = row[Trading212Column.CURRENCY_PRICE_PER_SHARE]
        self.exchange_rate = decimal_or_none(row, Trading212Column.EXCHANGE_RATE)

        if Trading212Column.TOTAL in row:
            amount = decimal_or_none(row, Trading212Column.TOTAL)
            currency = CurrencyCode(row[Trading212Column.CURRENCY_TOTAL])
        else:
            amount = decimal_or_none(row, Trading212Column.TOTAL_GBP)
            currency = CurrencyCode("GBP")

        if (
            amount is not None
            and (action == ActionType.BUY or self.raw_action == "Withdrawal")
            and amount > 0
        ):
            amount *= -1

        self.transaction_fee = Decimal(0)
        self.finra_fee = Decimal(0)
        self.stamp_duty = Decimal(0)
        self.conversion_fee = Decimal(0)
        foreign_fees: dict[CurrencyCode, Decimal] = {}
        for attr, fee_amount, fee_currency in self._read_fees(row):
            if fee_currency is None or fee_currency == currency:
                setattr(self, attr, getattr(self, attr) + fee_amount)
            else:
                fee_code = CurrencyCode(fee_currency)
                foreign_fees[fee_code] = (
                    foreign_fees.get(fee_code, Decimal(0)) + fee_amount
                )

        fees = (
            self.transaction_fee
            + self.finra_fee
            + self.conversion_fee
            + self.stamp_duty
        )

        # With fees pending conversion the price below is provisional;
        # the calculation engine re-derives it after folding them in.
        # A zero count leaves no price to derive, and dividing by it raises
        # before the row can be refused with something a reader can act on.
        price = (
            abs(amount + fees) / quantity if amount is not None and quantity else None
        )

        if (
            amount is not None
            and quantity
            and self.price_foreign is not None
            and (self.currency_foreign == "GBP" or self.exchange_rate is not None)
        ):
            exchange_rate = self.exchange_rate or Decimal(1)
            check_fees = self._checkable_fees(
                row, fees, foreign_fees, currency, exchange_rate
            )
            if check_fees is not None:
                check_price = abs(amount + check_fees) / quantity
                calculated_price_foreign = check_price * exchange_rate
                discrepancy = self.price_foreign - calculated_price_foreign
                if abs(discrepancy) > Decimal("0.015"):
                    LOGGER.warning(
                        "The Price per Share for this transaction after converting "
                        "and adding in the fees does not add up to the total amount. "
                        "You can fix the CSV by reviewing the transaction in the UI. "
                        "Discrepancy per Share: %.3f.",
                        float(discrepancy),
                    )

        isin_raw = row[Trading212Column.ISIN]
        isin = Isin(isin_raw) if isin_raw else None
        self.transaction_id = row.get(Trading212Column.TRANSACTION_ID)
        self.notes = row.get(Trading212Column.NOTES)
        # The row as it was exported. Pairing reads it to prove that a
        # reorganisation row fills nothing it should not. Deduplication does
        # not: it groups by the recorded transaction content instead, because
        # Trading 212 reuses ids and its exports have changed columns more
        # than once, so neither the id nor the raw cells identify a
        # transaction across exports.
        self.exported_row = row
        broker = "Trading212"
        super().__init__(
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
            isin,
            foreign_fees=foreign_fees,
        )

    def _checkable_fees(
        self,
        row: dict[Trading212Column, str],
        fees: Decimal,
        foreign_fees: dict[CurrencyCode, Decimal],
        currency: CurrencyCode,
        exchange_rate: Decimal,
    ) -> Decimal | None:
        """Total amounts to add back for the price consistency check.

        Withholding tax is deducted from a dividend Total while the Price
        per Share stays gross, so it is added back here. It is not a
        dealing cost and never reaches the reported fees.

        Foreign amounts in the instrument currency are converted with the
        export's own exchange rate. Returns None when one is in some other
        currency, which the export alone cannot convert.
        """
        total = fees
        foreign = dict(foreign_fees)
        withholding_tax = decimal_or_none(row, Trading212Column.WITHHOLDING_TAX)
        if withholding_tax:
            tax_currency = row.get(Trading212Column.CURRENCY_WITHHOLDING_TAX) or None
            if tax_currency is None or tax_currency == currency:
                total += withholding_tax
            else:
                tax_code = CurrencyCode(tax_currency)
                foreign[tax_code] = foreign.get(tax_code, Decimal(0)) + withholding_tax
        for fee_currency, fee_amount in foreign.items():
            if fee_currency != self.currency_foreign:
                return None
            total += fee_amount / exchange_rate
        return total

    @staticmethod
    def _read_fees(
        row: dict[Trading212Column, str],
    ) -> list[tuple[str, Decimal, str | None]]:
        """Read fee columns as (attribute, amount, currency) entries.

        A None currency means the export left it blank; only statutory
        taxes tolerate that (treated as the transaction currency).
        """
        entries: list[tuple[str, Decimal, str | None]] = []
        for fee_column in FEE_COLUMNS:
            if fee_column.gbp_column is not None:
                gbp_amount = decimal_or_none(row, fee_column.gbp_column) or Decimal(0)
                if gbp_amount:
                    entries.append((fee_column.attribute, gbp_amount, "GBP"))
            bare_amount = decimal_or_none(row, fee_column.bare_column) or Decimal(0)
            if bare_amount > 0:
                fee_currency = row.get(fee_column.currency_column) or None
                if fee_currency is None and fee_column.currency_required:
                    raise ValueError(
                        f"Missing currency for {fee_column.bare_column.value}"
                    )
                entries.append((fee_column.attribute, bare_amount, fee_currency))
        return entries

    @property
    def is_split_half(self) -> bool:
        """Whether this row is one half of a share reorganisation."""
        return self.raw_action in SPLIT_ACTIONS

    def recorded_fields(self) -> tuple[object, ...]:
        """Return the exported transaction content used to merge overlaps.

        The base dataclass contains the tax-relevant values common to every
        broker. Trading 212's timestamp, action label and foreign-currency
        values are additional recorded evidence, while its ID is deliberately
        absent: the broker can reuse one ID for distinct transactions.
        """
        recorded: list[object] = []
        for field in fields(self):
            if not field.compare:
                continue
            value = getattr(self, field.name)
            if isinstance(value, dict):
                value = tuple(sorted(value.items()))
            recorded.append(value)
        return (
            *recorded,
            self.datetime,
            self.raw_action,
            self.price_foreign,
            self.currency_foreign,
            self.exchange_rate,
            self.notes or "",
        )

    def where(self) -> str:
        """Name the file and line this row came from."""
        if self.source is None or self.source.file is None:
            return f"{self.raw_action} on {self.date}"
        return f"{self.source.file}, row {self.source.row}"


def _as_trading212(transaction: BrokerTransaction) -> Trading212Transaction:
    """Narrow a transaction this parser read from a Trading 212 export.

    Checked rather than asserted: `python -O` drops an assert, and what
    follows one here is an attribute only this class has, so the invariant
    would fail as an `AttributeError` from somewhere else entirely.
    """
    if not isinstance(transaction, Trading212Transaction):
        raise TypeError(
            f"{type(transaction).__name__} was not read from a Trading 212 export"
        )
    return transaction


@dataclass(frozen=True)
class SplitHalf:
    """One row of a share reorganisation, with its fields checked.

    `close` states the complete position before the event and `open` the
    complete position after it. Both restate one holding, so both carry the
    same value; nothing here is proceeds, expenditure or a new cost basis.
    """

    transaction: Trading212Transaction
    # The holding this row restates. Kept here rather than read back off the
    # transaction, where it is still `str | None`: `_read_split_half` refuses
    # a blank ticker, and carrying the proven value spares everything
    # downstream a check for a state that cannot reach it.
    symbol: str
    quantity: Decimal
    price: Decimal
    price_currency: str
    total: Decimal
    exchange_rate: Decimal

    @property
    def is_open(self) -> bool:
        """Whether this is the position after the event."""
        return self.transaction.raw_action == SPLIT_OPEN_ACTION

    @property
    def value(self) -> Decimal:
        """The position's value in the account currency.

        Each half is divided by its own exchange rate. The two halves of a
        pair can carry different rates, and because Trading 212 derives both
        rows from one account-currency total, those per-row rates are exactly
        what makes the two values agree. Pairing them the wrong way round
        inflates the disagreement a thousandfold and makes a sound tolerance
        look far too tight.
        """
        return self.quantity * self.price / self.exchange_rate


def _split_error(transaction: Trading212Transaction, message: str) -> ParsingError:
    """Build a parsing error pointing at the row that caused it."""
    source = transaction.source
    file = source.file if source is not None and source.file is not None else Path()
    row = source.row if source is not None else None
    return ParsingError(file, message, row_index=row)


def _exported_time(transaction: Trading212Transaction) -> str:
    """Return the timestamp spelling used by this export row."""
    row = transaction.exported_row
    return (
        row.get(Trading212Column.TIME)
        or row.get(Trading212Column.TIME_UTC)
        or transaction.datetime.isoformat()
    )


def _describe_half(half: SplitHalf) -> str:
    """Describe one pairing candidate with its source and checked fields."""
    transaction = half.transaction
    return (
        f"{transaction.raw_action} at {transaction.where()} "
        f"(Ticker={half.symbol!r}, No. of shares={half.quantity}, "
        f"Price / share={half.price} {half.price_currency}, "
        f"Exchange rate={half.exchange_rate}, Total={half.total} "
        f"{transaction.currency}, Time={_exported_time(transaction)})"
    )


def _split_period(halves: list[SplitHalf]) -> str:
    """Name every UK date covered by a set of pairing rows."""
    days = sorted({half.transaction.date for half in halves})
    return str(days[0]) if len(days) == 1 else f"{days[0]} through {days[-1]}"


def _split_pair_remedy(day: object) -> str:
    """Tell a user how to replace ambiguous or incomplete pairing evidence.

    The second sentence is not padding. A RAW `STOCK_SPLIT` row is what
    settles every other unprovable reorganisation, and it cannot settle this
    one: the refusal happens while the CSV is read, before the calculator
    assembles the day and reaches the RAW row that would override it. Saying
    only "re-export" leaves a reader whose export is already complete with
    nothing to do but edit the file, which the sentence before forbids.
    """
    return (
        "Check the named fields and rows against Trading 212, then replace "
        f"these files with one complete export covering {day}; do not delete "
        "or edit a row merely to force a match. If a complete export still "
        "states this, cgt-calc cannot work the day out and a RAW STOCK_SPLIT "
        "row cannot override it: leave this account out and work the day out "
        "by hand (consider professional advice)."
    )


def _check_split_has_no_money(transaction: Trading212Transaction) -> None:
    """Refuse financial evidence that contradicts a share reorganisation."""
    row = transaction.exported_row
    for column in SPLIT_ZERO_COLUMNS:
        # Several of these columns are read nowhere else, so this is their
        # first parse -- and it happens long after `read_transactions` has
        # stopped wrapping a bad cell in a `ParsingError`. Left bare, an
        # unreadable one reaches the user as "Unexpected error!" and a
        # traceback with no file and no row.
        try:
            value = decimal_or_none(row, column)
        except ValueError as err:
            raise _split_error(transaction, str(err)) from err
        if value is not None and (not value.is_finite() or value != 0):
            raise _split_error(
                transaction,
                f"{transaction.raw_action} states {column.value} of {value}. A "
                "share reorganisation moves no money and realises nothing, so "
                "this row is not one, or it is not supported. Check this row "
                "against Trading 212 and replace the export with a complete "
                f"one covering {transaction.date}.",
            )


def _read_split_half(transaction: Trading212Transaction) -> SplitHalf:
    """Check one reorganisation row and read the fields pairing needs."""
    symbol = transaction.symbol
    if not symbol:
        raise _split_error(
            transaction,
            f"{transaction.raw_action} does not say which holding it restates: "
            "the ticker is blank.",
        )
    quantity = transaction.quantity
    if quantity is None or not quantity.is_finite() or quantity <= 0:
        raise _split_error(
            transaction, f"{transaction.raw_action} needs a positive share count"
        )
    price = transaction.price_foreign
    if price is None or not price.is_finite() or price <= 0:
        raise _split_error(
            transaction, f"{transaction.raw_action} needs a positive price per share"
        )
    # A price is a number of some currency, and pairing multiplies it out
    # into a position value that is then compared against another row's.
    # An exchange rate does not supply the missing currency: it says how many
    # of one buy the other, not which two those are. Left unchecked, a rate
    # of 1 was enough to let two halves reconcile on figures neither row said
    # the currency of.
    if transaction.currency_foreign in {"", "Not available"}:
        raise _split_error(
            transaction,
            f"{transaction.raw_action} states a price of {price} but leaves "
            f"{Trading212Column.CURRENCY_PRICE_PER_SHARE.value} "
            f"{transaction.currency_foreign!r}. A price with no currency "
            "cannot be checked against the total. Re-export the range "
            f"covering {transaction.date} with every column included.",
        )
    total = transaction.amount
    # Zero is allowed: a small enough position rounds to nothing in the
    # account currency. Negative is not; the position has a value.
    if total is None or not total.is_finite() or total < 0:
        raise _split_error(
            transaction,
            f"{transaction.raw_action} needs a total of zero or more, in the "
            "account currency",
        )
    rate = transaction.exchange_rate
    if rate is None:
        if transaction.currency_foreign != transaction.currency:
            raise _split_error(
                transaction,
                f"{transaction.raw_action} is priced in "
                f"{transaction.currency_foreign} and totalled in "
                f"{transaction.currency} but states no exchange rate",
            )
        rate = Decimal(1)
    elif not rate.is_finite() or rate <= 0:
        raise _split_error(
            transaction, f"{transaction.raw_action} needs a positive exchange rate"
        )
    _check_split_has_no_money(transaction)
    return SplitHalf(
        transaction=transaction,
        symbol=symbol,
        quantity=quantity,
        price=price,
        price_currency=transaction.currency_foreign,
        total=total,
        exchange_rate=rate,
    )


def _half_incompatibilities(open_half: SplitHalf, close_half: SplitHalf) -> list[str]:
    """Name every check that prevents two rows forming one reorganisation.

    A differing identifier is deliberately not tested here. Two rows for one
    ticker on one day under two identifiers are a reorganisation that changes
    the security as well, and saying so is more use than leaving them
    unpaired for no stated reason.
    """
    issues: list[str] = []
    gap = open_half.transaction.datetime - close_half.transaction.datetime
    if not timedelta(0) <= gap <= SPLIT_PAIR_MAX_GAP:
        issues.append(
            f"Time: {SPLIT_CLOSE_ACTION}="
            f"{_exported_time(close_half.transaction)}; {SPLIT_OPEN_ACTION}="
            f"{_exported_time(open_half.transaction)} (the open must be from "
            "zero to one second after the close)"
        )
    # Two already-rounded account-currency figures, so no relative part.
    if not approx_equal(open_half.total, close_half.total, TOTAL_ABS_TOLERANCE):
        issues.append(
            f"Total: {SPLIT_CLOSE_ACTION}={close_half.total}; "
            f"{SPLIT_OPEN_ACTION}={open_half.total}"
        )
    issues.extend(
        (
            f"{half.transaction.raw_action} calculated position value="
            f"{half.value} {half.transaction.currency}; Total={half.total} "
            f"{half.transaction.currency}"
        )
        for half in (open_half, close_half)
        if not approx_equal_scaled(
            half.value,
            half.total,
            reference=half.total,
            rel_tolerance=VALUE_REL_TOLERANCE,
            abs_tolerance=TOTAL_ABS_TOLERANCE,
        )
    )
    if not approx_equal_scaled(
        open_half.value,
        close_half.value,
        reference=max(abs(open_half.value), abs(close_half.value)),
        rel_tolerance=VALUE_REL_TOLERANCE,
    ):
        issues.append(
            f"calculated position value: {SPLIT_CLOSE_ACTION}="
            f"{close_half.value}; {SPLIT_OPEN_ACTION}={open_half.value}"
        )
    return issues


class Trading212Parser(BaseDirParser[BrokerTransaction]):
    """Trading 212 parser."""

    arg_name = "trading212"
    pretty_name = "Trading 212"
    format_name = "CSV"
    glob_dir = "*.csv"
    deprecated_flags: ClassVar[list[str]] = ["--trading212"]

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Parse Trading 212 transactions from CSV file."""
        lines = list(csv.reader(file))
        if not lines:
            raise ParsingError(file_path, "Trading 212 CSV file is empty")
        header = lines[0]
        cls._validate_header(header, file_path)
        lines = lines[1:]
        transactions: list[BrokerTransaction] = []
        for index, row in enumerate(lines, start=2):
            try:
                transaction = Trading212Transaction(header, row, file_path)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
            transaction.source = TransactionSource(
                row=index, timestamp=transaction.datetime
            )
            transactions.append(transaction)
        return transactions

    @staticmethod
    def _validate_header(header: list[str], file: Path) -> None:
        """Check if header is valid. Not all columns exist in every export."""
        unknown = set(header) - COLUMN_SET
        if unknown:
            raise ParsingError(
                file, f"Unknown column(s) {', '.join(sorted(unknown))}", row_index=1
            )

    @staticmethod
    def _by_date_and_action(
        transaction: BrokerTransaction,
    ) -> tuple[datetime, bool]:
        """Sort by the instant the export stated, and by action type."""

        if isinstance(transaction, StockSplitTransaction):
            # A reorganisation, which is two rows combined into one event.
            # It exists from the instant of its `open` half.
            instant = max(transaction.event.instants)
        else:
            instant = _as_trading212(transaction).datetime

        # If there's a deposit in the same second as a buy
        # (happens with the referral award at least)
        # we want to put the buy last to avoid negative balance errors
        return (instant, transaction.action == ActionType.BUY)

    @classmethod
    @override
    def post_process_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Collapse rows two exports both report, and put them in order."""
        return sorted(cls._deduplicate(transactions), key=cls._by_date_and_action)

    @classmethod
    def _deduplicate(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Merge overlapping exports without losing within-file multiplicity.

        Exports are requested by date range, so consecutive downloads
        routinely cover the same days and report the same transaction twice.
        For matching recorded content, the largest count in any one export is
        authoritative. Distinct nonblank IDs can prove additional fills, but
        an ID never joins rows with different content: Trading 212 reuses IDs.

        Every split row is checked before any overlap copy can be dropped, so
        a richer schema cannot hide a cash leg or other contrary evidence.
        """
        groups: dict[tuple[object, ...], list[Trading212Transaction]] = defaultdict(
            list
        )
        for row in transactions:
            transaction = _as_trading212(row)
            if transaction.is_split_half:
                _read_split_half(transaction)
            elif transaction.action is ActionType.STOCK_SPLIT:
                _check_split_has_no_money(transaction)
            groups[transaction.recorded_fields()].append(transaction)

        kept: list[BrokerTransaction] = []
        for group in groups.values():
            per_export = Counter(
                transaction.source.file if transaction.source is not None else None
                for transaction in group
            )
            identified: dict[str, Trading212Transaction] = {}
            for transaction in group:
                if transaction.transaction_id:
                    identified.setdefault(transaction.transaction_id, transaction)

            # One row per id the export tells apart, then topped up to the
            # largest count any single export reported. `keep_count` caps the
            # top-up, never the rows already seeded: the ids are evidence in
            # their own right. The comparison is `>=` so that stays true by
            # construction -- with `==`, a `keep_count` that ever fell below
            # the seed would sail past the break and keep the whole group,
            # turning "at most one copy per export" into "every copy".
            keep_count = max(*per_export.values(), len(identified))
            chosen = list(identified.values())
            chosen_objects = {id(transaction) for transaction in chosen}
            for transaction in group:
                if len(chosen) >= keep_count:
                    break
                if id(transaction) not in chosen_objects:
                    chosen.append(transaction)
                    chosen_objects.add(id(transaction))
            kept.extend(chosen)
        return kept

    @classmethod
    @override
    def finalize_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Combine each pair of reorganisation rows into one event.

        This has to see the whole directory at once. The two halves can land
        in different exports, and `post_process_transactions` runs per file
        as well, so pairing there would reject a half whose partner is in the
        next file.
        """
        halves = [
            transaction
            for transaction in transactions
            if isinstance(transaction, Trading212Transaction)
            and transaction.is_split_half
        ]
        if not halves:
            return transactions
        # By identity: two halves of one reorganisation can be identical rows
        # apart from their action, and comparing by value would drop one.
        paired = {id(half) for half in halves}
        rest = [
            transaction for transaction in transactions if id(transaction) not in paired
        ]
        split_halves = [_read_split_half(half) for half in halves]
        legacy_splits = [
            _as_trading212(transaction)
            for transaction in rest
            if transaction.action is ActionType.STOCK_SPLIT
        ]
        events = cls._pair_split_halves(split_halves, legacy_splits)
        for event in events:
            cls._refuse_rows_between_halves(event, rest)
        return sorted([*rest, *events], key=cls._by_date_and_action)

    @staticmethod
    def _split_candidates(halves: list[SplitHalf]) -> list[list[SplitHalf]]:
        """Group unresolved halves only far enough to write one useful error.

        Event ownership is settled before this diagnostic window is used.
        Each group is anchored at its first row rather than extended from its
        last, so a chain of unrelated rows cannot grow it without bound.

        The account bucket has exactly one key today: `finalize_transactions`
        runs once per declared boundary, and every file in one runs under one
        token. It is kept as the guard it is rather than dropped, because the
        rows it would otherwise put in one error belong to accounts the docs
        promise are never read together -- but it is a guard, not a grouping,
        so nothing here relies on it separating anything.
        """
        by_account: dict[object, list[SplitHalf]] = defaultdict(list)
        for half in halves:
            source = half.transaction.source
            by_account[source.account if source is not None else None].append(half)

        clusters: list[list[SplitHalf]] = []
        for account_halves in by_account.values():
            # Stable, so halves stamped at one instant keep the order they
            # were read in and the grouping is deterministic.
            account_halves.sort(key=lambda half: half.transaction.datetime)
            cluster: list[SplitHalf] = []
            for half in account_halves:
                if (
                    cluster
                    and half.transaction.datetime - cluster[0].transaction.datetime
                    > SPLIT_CANDIDATE_WINDOW
                ):
                    clusters.append(cluster)
                    cluster = []
                cluster.append(half)
            clusters.append(cluster)
        return clusters

    @classmethod
    def _pair_split_halves(
        cls,
        halves: list[SplitHalf],
        legacy_splits: list[Trading212Transaction],
    ) -> list[StockSplitTransaction]:
        """Pair every row only when the strict evidence has one full matching."""
        possibilities: dict[int, list[SplitHalf]] = {id(half): [] for half in halves}
        opens = [half for half in halves if half.is_open]
        closes = [half for half in halves if not half.is_open]
        for close_half in closes:
            close_source = close_half.transaction.source
            close_key = (
                close_source.account if close_source is not None else None,
                close_half.symbol,
                close_half.price_currency,
                close_half.transaction.currency,
            )
            for open_half in opens:
                open_source = open_half.transaction.source
                open_key = (
                    open_source.account if open_source is not None else None,
                    open_half.symbol,
                    open_half.price_currency,
                    open_half.transaction.currency,
                )
                if close_key == open_key and not _half_incompatibilities(
                    open_half, close_half
                ):
                    possibilities[id(close_half)].append(open_half)
                    possibilities[id(open_half)].append(close_half)

        pairs: list[tuple[SplitHalf, SplitHalf]] = []
        unresolved: list[SplitHalf] = []
        visited: set[int] = set()
        for half in halves:
            if id(half) in visited:
                continue
            if not possibilities[id(half)]:
                visited.add(id(half))
                unresolved.append(half)
                continue
            component: list[SplitHalf] = []
            pending = [half]
            while pending:
                candidate = pending.pop()
                if id(candidate) in visited:
                    continue
                visited.add(id(candidate))
                component.append(candidate)
                pending.extend(possibilities[id(candidate)])
            # Exactly one close and one open, or refuse. Edges only join a
            # close to an open, so a two-row component is necessarily one of
            # each. A larger component means several rows share the same
            # evidence, and searching it for a unique matching is a factorial
            # walk on rows deduplication legitimately keeps (distinct ids
            # across overlapping exports), while the calculator refuses more
            # than one reorganisation of one security on one date anyway.
            if len(component) == HALVES_PER_SPLIT:
                open_half = next(half for half in component if half.is_open)
                close_half = next(half for half in component if not half.is_open)
                pairs.append((open_half, close_half))
            else:
                cls._refuse_unpaired_group(component, component)

        events: list[StockSplitTransaction] = []
        for open_half, close_half in pairs:
            cls._refuse_legacy_split(open_half, close_half, legacy_splits)
            events.append(cls._combine(open_half, close_half))

        for candidates in cls._split_candidates(unresolved):
            opens = [half for half in candidates if half.is_open]
            closes = [half for half in candidates if not half.is_open]
            if len(opens) == 1 and len(closes) == 1:
                open_half, close_half = opens[0], closes[0]
                pairing_fields = (
                    (
                        Trading212Column.TICKER.value,
                        close_half.symbol,
                        open_half.symbol,
                    ),
                    (
                        Trading212Column.CURRENCY_PRICE_PER_SHARE.value,
                        close_half.price_currency,
                        open_half.price_currency,
                    ),
                    (
                        Trading212Column.CURRENCY_TOTAL.value,
                        close_half.transaction.currency,
                        open_half.transaction.currency,
                    ),
                )
                disagreements = [
                    f"{field}: {SPLIT_CLOSE_ACTION}={close!r}; "
                    f"{SPLIT_OPEN_ACTION}={after!r}"
                    for field, close, after in pairing_fields
                    if close != after
                ]
                if disagreements:
                    row = open_half.transaction
                    raise _split_error(
                        row,
                        f"The only {SPLIT_CLOSE_ACTION} and {SPLIT_OPEN_ACTION} "
                        f"rows stamped together around {row.date} disagree "
                        f"about {'; '.join(disagreements)}. Rows: "
                        f"{_describe_half(close_half)}; "
                        f"{_describe_half(open_half)}. "
                        f"{_split_pair_remedy(_split_period(candidates))}",
                    )

            groups: dict[tuple[object, ...], list[SplitHalf]] = defaultdict(list)
            for half in candidates:
                source = half.transaction.source
                groups[
                    source.account if source is not None else None,
                    half.symbol,
                    half.price_currency,
                    half.transaction.currency,
                ].append(half)
            for group in groups.values():
                related = [
                    half for half in candidates if half.symbol == group[0].symbol
                ]
                cls._refuse_unpaired_group(group, related)
        return events

    @staticmethod
    def _refuse_legacy_split(
        open_half: SplitHalf,
        close_half: SplitHalf,
        legacy_splits: list[Trading212Transaction],
    ) -> None:
        """Refuse old and new export shapes that may state one event twice.

        Both of the pair's UK dates count, not just the one the event ends up
        filed under. A pair stamped either side of UK midnight puts its close
        on the previous day, and a legacy row on that day, more than a window
        from either half, once escaped both tests and let the same
        consolidation be applied a second time -- silently, because the
        calculator plans each day on its own and never saw the two together.
        """
        pair_dates = {
            open_half.transaction.date,
            close_half.transaction.date,
        }
        conflicts = [
            row
            for row in legacy_splits
            if row.symbol == open_half.symbol
            and (
                row.date in pair_dates
                or min(
                    abs(row.datetime - open_half.transaction.datetime),
                    abs(row.datetime - close_half.transaction.datetime),
                )
                <= SPLIT_CANDIDATE_WINDOW
            )
        ]
        if not conflicts:
            return
        rows = [open_half.transaction, close_half.transaction, *conflicts]
        days = sorted({row.date for row in rows})
        period = str(days[0]) if len(days) == 1 else f"{days[0]} through {days[-1]}"
        row = conflicts[0]
        legacy = "; ".join(
            f"{split.raw_action} at {split.where()} "
            f"(Ticker={split.symbol!r}, No. of shares={split.quantity}, "
            f"Time={_exported_time(split)})"
            for split in conflicts
        )
        raise _split_error(
            row,
            f"{row.symbol} is reported as both Stock Split and Stock split "
            "close/open. They may describe one corporate action, so applying "
            "both could restate the holding twice, while discarding either "
            f"could erase a separate event. Rows: {legacy}; "
            f"{_describe_half(close_half)}; {_describe_half(open_half)}. "
            f"{_split_pair_remedy(period)}",
        )

    @staticmethod
    def _refuse_unpaired_group(
        group: list[SplitHalf], candidates: list[SplitHalf]
    ) -> NoReturn:
        """Refuse rows the ownership matcher could not pair.

        A half without its partner says nothing about what happened to the
        holding, and a larger group has no reading worth guessing.
        ``candidates`` contains only the nearby rows for this holding, so the
        error cannot implicate an unrelated security.
        """
        opens = [half for half in group if half.is_open]
        closes = [half for half in group if not half.is_open]
        if len(opens) != 1 or len(closes) != 1:
            row = (opens or closes)[0].transaction
            period = _split_period(candidates)
            when = f"on {period}" if " through " not in period else f"across {period}"
            raise _split_error(
                row,
                f"{row.symbol} {when} has {len(closes)} "
                f"{SPLIT_CLOSE_ACTION} and {len(opens)} {SPLIT_OPEN_ACTION} "
                "rows. A reorganisation is exported as the position before it "
                "and the position after it, once each. Candidate rows: "
                f"{'; '.join(_describe_half(half) for half in candidates)}. "
                f"{_split_pair_remedy(period)}",
            )
        open_half, close_half = opens[0], closes[0]
        incompatibilities = _half_incompatibilities(open_half, close_half)
        detail = "; ".join(incompatibilities) or "there is no unique pairing"
        row = open_half.transaction
        raise _split_error(
            row,
            f"The {SPLIT_OPEN_ACTION} and {SPLIT_CLOSE_ACTION} rows for "
            f"{row.symbol} on {row.date} cannot be proved to be the two "
            f"halves of one event: {detail}. Rows: "
            f"{_describe_half(close_half)}; {_describe_half(open_half)}. "
            f"{_split_pair_remedy(_split_period(group))}",
        )

    @classmethod
    def _combine(
        cls, open_half: SplitHalf, close_half: SplitHalf
    ) -> StockSplitTransaction:
        """Build the one event these two rows describe."""
        after = open_half.transaction
        before = close_half.transaction
        if (
            before.isin is not None
            and after.isin is not None
            and before.isin != after.isin
        ):
            raise _split_error(
                after,
                f"The reorganisation of {after.symbol} on {after.date} changes "
                f"the ISIN from {before.isin} to {after.isin}, which is a "
                "different security. Moving a holding between two securities "
                "needs authoritative relationship data this cannot get from "
                f"the export. Rows: {close_half.transaction.where()}; "
                f"{open_half.transaction.where()}. "
                f"{_split_pair_remedy(_split_period([close_half, open_half]))}",
            )
        ratio = recover_ratio(close_half.quantity, open_half.quantity)
        if isinstance(ratio, UnresolvedRatio):
            # Not a parsing failure. A holding the export states in full is
            # replaced outright and needs no ratio, so refusing the whole
            # file here would reject it for a number nothing asked for. The
            # calculator refuses where the ratio is actually used.
            LOGGER.debug(
                "No single ratio for the %s reorganisation on %s: %s",
                after.symbol,
                after.date,
                ratio.describe(),
            )
        # Proven non-blank when the half was read, so no check here.
        symbol = open_half.symbol
        event = StockSplitEvent(
            date=after.date,
            broker=after.broker,
            symbol_before=symbol,
            symbol_after=symbol,
            isin_before=before.isin,
            isin_after=after.isin,
            before_quantity=close_half.quantity,
            after_quantity=open_half.quantity,
            ratio=ratio,
            instants=(before.datetime, after.datetime),
        )
        return StockSplitTransaction(
            event=event,
            description=after.description,
            currency=after.currency,
            source=after.source,
        )

    @staticmethod
    def _refuse_rows_between_halves(
        event: StockSplitTransaction, others: list[BrokerTransaction]
    ) -> None:
        """Refuse a trade stamped at or between the two halves.

        Such a row is on neither side of the reorganisation, so whether its
        count is stated in the units before it or the units after it cannot
        be proved.
        """
        first = min(event.event.instants)
        last = max(event.event.instants)
        for row in others:
            if row.symbol != event.symbol or quantity_sign(row.action) == 0:
                continue
            other = _as_trading212(row)
            if first <= other.datetime <= last:
                raise _split_error(
                    other,
                    f"This row is stamped between the two halves of the "
                    f"{event.symbol} reorganisation on {event.date}, so there "
                    "is no telling whether its share count is stated in the "
                    f"units before it or the units after it. Rows: "
                    f"{other.raw_action} at {other.where()} "
                    f"(No. of shares={other.quantity}, "
                    f"Time={_exported_time(other)}); the reorganisation at "
                    f"{first} and {last}. Check this row against Trading 212; "
                    "if it really was dealt while the reorganisation was in "
                    f"flight, leave {event.symbol} out and work it out by hand "
                    "(consider professional advice).",
                )
