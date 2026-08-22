"""Trading 212 parser."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction, Isin

from .base_parsers import BaseDirParser

if TYPE_CHECKING:
    from pathlib import Path


class Trading212Column(StrEnum):
    """Columns exported in the Trading 212 transaction CSV."""

    ACTION = "Action"
    TIME = "Time"
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


COLUMNS: Final[list[str]] = [column.value for column in Trading212Column]
COLUMN_SET: Final[set[str]] = {column.value for column in Trading212Column}
LOGGER = logging.getLogger(__name__)


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
    if value is None or value in ("", "Not available"):
        return None
    try:
        return Decimal(value)
    except InvalidOperation as err:
        raise ValueError(f"Invalid decimal in {column.value}: {value!r}") from err


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert label to ActionType."""
    if label in [
        "Market buy",
        "Limit buy",
        "Stop buy",
        "Stop limit buy",
    ]:
        return ActionType.BUY

    if label in [
        "Market sell",
        "Limit sell",
        "Stop sell",
        "Stop limit sell",
    ]:
        return ActionType.SELL

    if label in [
        "Card credit",
        "Card debit",
        "Card refund",
        "Deposit",
        "Withdrawal",
    ]:
        return ActionType.TRANSFER

    if label in [
        "Dividend (Ordinary)",
        "Dividend (Dividend)",
        "Dividend (Dividends paid by us corporations)",
        "Dividend (Dividend manufactured payment)",
        "Dividend (Property income distribution)",
        "Dividend adjustment",
    ]:
        return ActionType.DIVIDEND

    if label in [
        "Interest on cash",
        "Lending interest",
        # Interest distributions from bond and money market funds,
        # taxed as savings income rather than dividends.
        "Dividend (Interest)",
    ]:
        return ActionType.INTEREST

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label == "Spin off":
        return ActionType.SPIN_OFF

    if label in [
        "Currency conversion",
        "Result adjustment",
        "Spending cashback",
    ]:
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

        time_str = row[Trading212Column.TIME]
        time_format = "%Y-%m-%d %H:%M:%S.%f" if "." in time_str else "%Y-%m-%d %H:%M:%S"
        self.datetime = datetime.strptime(time_str, time_format)
        date = self.datetime.date()
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
            currency = row[Trading212Column.CURRENCY_TOTAL]
        else:
            amount = decimal_or_none(row, Trading212Column.TOTAL_GBP)
            currency = "GBP"

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
        foreign_fees: dict[str, Decimal] = {}
        for attr, fee_amount, fee_currency in self._read_fees(row):
            if fee_currency is None or fee_currency == currency:
                setattr(self, attr, getattr(self, attr) + fee_amount)
            else:
                foreign_fees[fee_currency] = (
                    foreign_fees.get(fee_currency, Decimal(0)) + fee_amount
                )

        fees = (
            self.transaction_fee
            + self.finra_fee
            + self.conversion_fee
            + self.stamp_duty
        )

        # With fees pending conversion the price below is provisional;
        # the calculation engine re-derives it after folding them in.
        price = (
            abs(amount + fees) / quantity
            if amount is not None and quantity is not None
            else None
        )

        if (
            amount is not None
            and quantity is not None
            and self.price_foreign is not None
            and (self.currency_foreign == "GBP" or self.exchange_rate is not None)
        ):
            exchange_rate = self.exchange_rate or Decimal(1)
            check_fees = self._checkable_fees(fees, foreign_fees, exchange_rate)
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
        fees: Decimal,
        foreign_fees: dict[str, Decimal],
        exchange_rate: Decimal,
    ) -> Decimal | None:
        """Total fees for the price consistency check.

        Foreign fees in the instrument currency are converted with the
        export's own exchange rate. Returns None when a fee is in some
        other currency, which the export alone cannot convert.
        """
        total = fees
        for fee_currency, fee_amount in foreign_fees.items():
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

    @override
    def __hash__(self) -> int:
        """Calculate hash."""
        return hash(self.transaction_id)


class Trading212Parser(BaseDirParser):
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
                transactions.append(Trading212Transaction(header, row, file_path))
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
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
        transaction: Trading212Transaction,
    ) -> tuple[datetime, bool]:
        """Sort by date and action type."""

        # If there's a deposit in the same second as a buy
        # (happens with the referral award at least)
        # we want to put the buy last to avoid negative balance errors
        return (transaction.datetime, transaction.action == ActionType.BUY)

    @classmethod
    @override
    def post_process_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Remove duplicates and sort."""
        transactions = list(set(transactions))
        transactions.sort(key=cls._by_date_and_action)  # type: ignore[arg-type]
        return transactions
