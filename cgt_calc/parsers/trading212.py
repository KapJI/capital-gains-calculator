"""Trading 212 parser."""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from pathlib import Path
from typing import ClassVar, Final, TextIO, override

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

# The two halves are stamped at the same instant or milliseconds apart.
SPLIT_PAIR_MAX_GAP: Final = timedelta(seconds=1)

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
        # The row as it was exported. Deduplication compares transactions
        # that share an id cell for cell, and a summary of the fields this
        # parser happens to read would let two rows differing only in one it
        # ignores collapse into one. Pairing reads it too, to prove that a
        # reorganisation row fills nothing it should not.
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

    def disagreements(self, other: Trading212Transaction) -> list[str]:
        """Columns where two rows sharing an id state different things.

        Only columns both exports carry are compared, blanks included.
        Trading 212 has changed its columns more than once, so a column one
        export has and the other does not says nothing; a column both have,
        filled differently, says they are not the same transaction.
        """
        shared = self.exported_row.keys() & other.exported_row.keys()
        return sorted(
            column.value
            for column in shared
            if self.exported_row[column] != other.exported_row[column]
        )

    def where(self) -> str:
        """Name the file and line this row came from."""
        if self.source is None or self.source.file is None:
            return f"{self.raw_action} on {self.date}"
        return f"{self.source.file}, row {self.source.row}"

    @override
    def __hash__(self) -> int:
        """Calculate hash."""
        return hash(self.transaction_id)


@dataclass(frozen=True)
class SplitHalf:
    """One row of a share reorganisation, with its fields checked.

    `close` states the complete position before the event and `open` the
    complete position after it. Both restate one holding, so both carry the
    same value; nothing here is proceeds, expenditure or a new cost basis.
    """

    transaction: Trading212Transaction
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


def _read_split_half(transaction: Trading212Transaction) -> SplitHalf:
    """Check one reorganisation row and read the fields pairing needs."""
    row = transaction.exported_row
    if not transaction.symbol:
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
    for column in SPLIT_ZERO_COLUMNS:
        value = decimal_or_none(row, column)
        if value is not None and (not value.is_finite() or value != 0):
            raise _split_error(
                transaction,
                f"{transaction.raw_action} states {column.value} of {value}. A "
                "share reorganisation moves no money and realises nothing, so "
                "this row is not one, or it is not supported.",
            )
    return SplitHalf(
        transaction=transaction,
        quantity=quantity,
        price=price,
        price_currency=transaction.currency_foreign,
        total=total,
        exchange_rate=rate,
    )


def _halves_are_compatible(open_half: SplitHalf, close_half: SplitHalf) -> bool:
    """Whether these two rows can be the two halves of one reorganisation.

    A differing identifier is deliberately not tested here. Two rows for one
    ticker on one day under two identifiers are a reorganisation that changes
    the security as well, and saying so is more use than leaving them
    unpaired for no stated reason.
    """
    gap = open_half.transaction.datetime - close_half.transaction.datetime
    if not timedelta(0) <= gap <= SPLIT_PAIR_MAX_GAP:
        return False
    # Two already-rounded account-currency figures, so no relative part.
    if not approx_equal(open_half.total, close_half.total, TOTAL_ABS_TOLERANCE):
        return False
    for half in (open_half, close_half):
        if not approx_equal_scaled(
            half.value,
            half.total,
            reference=half.total,
            rel_tolerance=VALUE_REL_TOLERANCE,
            abs_tolerance=TOTAL_ABS_TOLERANCE,
        ):
            return False
    return approx_equal_scaled(
        open_half.value,
        close_half.value,
        reference=max(abs(open_half.value), abs(close_half.value)),
        rel_tolerance=VALUE_REL_TOLERANCE,
    )


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

        if isinstance(transaction, Trading212Transaction):
            instant = transaction.datetime
        else:
            # A reorganisation, which is two rows combined into one event.
            # It exists from the instant of its `open` half.
            assert isinstance(transaction, StockSplitTransaction)
            instant = max(transaction.event.instants)

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
        """Keep one copy of each transaction the export identifies.

        Exports are requested by date range, so consecutive downloads
        routinely cover the same days and report the same transaction twice.
        Two rows sharing an id state one transaction, so they must agree
        about it; a blank id identifies nothing, and two legitimate events
        can then be indistinguishable, so nothing is collapsed on whole-row
        equality alone.
        """
        canonical: dict[str, Trading212Transaction] = {}
        kept: list[BrokerTransaction] = []
        for transaction in transactions:
            assert isinstance(transaction, Trading212Transaction)
            identifier = transaction.transaction_id
            if not identifier:
                kept.append(transaction)
                continue
            seen = canonical.get(identifier)
            if seen is None:
                canonical[identifier] = transaction
                kept.append(transaction)
                continue
            disagreements = seen.disagreements(transaction)
            if disagreements:
                raise _split_error(
                    transaction,
                    f"Transaction {identifier} is also at {seen.where()}, and "
                    f"the two rows disagree about {', '.join(disagreements)}. "
                    "One id is one transaction, so there is no telling which "
                    "of these it is.",
                )
            # The first copy read is the canonical one, which is
            # deterministic: files are globbed in sorted order, read in row
            # order.
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
        events = cls._pair_split_halves([_read_split_half(half) for half in halves])
        for event in events:
            cls._refuse_rows_between_halves(event, rest)
        return sorted([*rest, *events], key=cls._by_date_and_action)

    @classmethod
    def _pair_split_halves(cls, halves: list[SplitHalf]) -> list[StockSplitTransaction]:
        """Pair every reorganisation row with the row that completes it."""
        groups: dict[tuple[object, ...], list[SplitHalf]] = defaultdict(list)
        for half in halves:
            transaction = half.transaction
            source = transaction.source
            groups[
                source.account if source is not None else None,
                transaction.date,
                transaction.symbol,
                half.price_currency,
                transaction.currency,
            ].append(half)
        return [event for group in groups.values() for event in cls._pair_group(group)]

    @classmethod
    def _pair_group(cls, group: list[SplitHalf]) -> list[StockSplitTransaction]:
        """Pair the two reorganisation rows for one security on one day.

        Exactly one of each. A half without its partner says nothing about
        what happened to the holding, and the calculator refuses two
        reorganisations of one security on one date in any case, so a larger
        group has no reading worth searching for.
        """
        opens = [half for half in group if half.is_open]
        closes = [half for half in group if not half.is_open]
        if len(opens) != 1 or len(closes) != 1:
            row = (opens or closes)[0].transaction
            raise _split_error(
                row,
                f"{row.symbol} on {row.date} has {len(closes)} "
                f"{SPLIT_CLOSE_ACTION} and {len(opens)} {SPLIT_OPEN_ACTION} "
                "rows. A reorganisation is exported as the position before it "
                "and the position after it, once each.",
            )
        open_half, close_half = opens[0], closes[0]
        if not _halves_are_compatible(open_half, close_half):
            row = open_half.transaction
            raise _split_error(
                row,
                f"The {SPLIT_OPEN_ACTION} and {SPLIT_CLOSE_ACTION} rows for "
                f"{row.symbol} on {row.date} are not the two halves of one "
                "event. Their totals, position values or timestamps disagree, "
                "and pairing them anyway would restate the holding by a ratio "
                "nothing in the export supports.",
            )
        return [cls._combine(open_half, close_half)]

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
                "the export.",
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
        symbol = after.symbol
        assert symbol is not None
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
        for other in others:
            if other.symbol != event.symbol or quantity_sign(other.action) == 0:
                continue
            assert isinstance(other, Trading212Transaction)
            if first <= other.datetime <= last:
                raise _split_error(
                    other,
                    f"This row is stamped between the two halves of the "
                    f"{event.symbol} reorganisation on {event.date}, so there "
                    "is no telling whether its share count is stated in the "
                    "units before it or the units after it.",
                )
