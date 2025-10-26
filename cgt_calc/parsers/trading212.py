"""Trading 212 parser."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

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
    TRANSACTION_FEE_GBP = "Transaction fee (GBP)"
    TRANSACTION_FEE = "Transaction fee"
    FINRA_FEE_GBP = "Finra fee (GBP)"
    FINRA_FEE = "Finra fee"
    STAMP_DUTY_GBP = "Stamp duty (GBP)"
    NOTES = "Notes"
    TRANSACTION_ID = "ID"
    CURRENCY_CONVERSION_FEE_GBP = "Currency conversion fee (GBP)"
    CURRENCY_CONVERSION_FEE = "Currency conversion fee"
    CURRENCY_CURRENCY_CONVERSION_FEE = "Currency (Currency conversion fee)"
    CURRENCY_TRANSACTION_FEE = "Currency (Transaction fee)"
    CURRENCY_FINRA_FEE = "Currency (Finra fee)"


COLUMNS: Final[list[str]] = [column.value for column in Trading212Column]
COLUMN_SET: Final[set[str]] = {column.value for column in Trading212Column}
LOGGER = logging.getLogger(__name__)


def decimal_or_none(
    row: dict[Trading212Column, str], column: Trading212Column
) -> Decimal | None:
    """Convert a column value to Decimal or return None when blank."""

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
    ]:
        return ActionType.BUY

    if label in [
        "Market sell",
        "Limit sell",
        "Stop sell",
    ]:
        return ActionType.SELL

    if label in [
        "Deposit",
        "Withdrawal",
    ]:
        return ActionType.TRANSFER

    if label in [
        "Dividend (Ordinary)",
        "Dividend (Dividend)",
        "Dividend (Dividends paid by us corporations)",
    ]:
        return ActionType.DIVIDEND

    if label in [
        "Interest on cash",
        "Lending interest",
    ]:
        return ActionType.INTEREST

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label in ["Result adjustment"]:
        return ActionType.ADJUSTMENT

    raise ParsingError(file, f"Unknown action: {label}")


class Trading212Transaction(BrokerTransaction):
    """Represent single Trading 212 transaction."""

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

        self.transaction_fee = decimal_or_none(
            row, Trading212Column.TRANSACTION_FEE_GBP
        ) or Decimal(0)

        transaction_fee_foreign = decimal_or_none(
            row, Trading212Column.TRANSACTION_FEE
        ) or Decimal(0)
        if transaction_fee_foreign > 0:
            if row.get(Trading212Column.CURRENCY_TRANSACTION_FEE) != "GBP":
                raise ParsingError(
                    file,
                    "The transaction fee is not in GBP which is not supported yet",
                )
            self.transaction_fee += transaction_fee_foreign

        self.finra_fee = decimal_or_none(
            row, Trading212Column.FINRA_FEE_GBP
        ) or Decimal(0)

        finra_fee_foreign = decimal_or_none(row, Trading212Column.FINRA_FEE) or Decimal(
            0
        )
        if finra_fee_foreign > 0:
            if row.get(Trading212Column.CURRENCY_FINRA_FEE) != "GBP":
                raise ParsingError(
                    file,
                    "Finra fee is not in GBP which is not supported yet",
                )
            self.finra_fee += finra_fee_foreign

        self.stamp_duty = decimal_or_none(
            row, Trading212Column.STAMP_DUTY_GBP
        ) or Decimal(0)

        self.conversion_fee = decimal_or_none(
            row, Trading212Column.CURRENCY_CONVERSION_FEE_GBP
        ) or Decimal(0)

        conversion_fee_foreign = decimal_or_none(
            row, Trading212Column.CURRENCY_CONVERSION_FEE
        ) or Decimal(0)
        if conversion_fee_foreign > 0:
            if row.get(Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE) != "GBP":
                raise ParsingError(
                    file,
                    "The transaction fee is not in GBP which is not supported yet",
                )
            self.conversion_fee += conversion_fee_foreign

        fees = self.transaction_fee + self.finra_fee + self.conversion_fee

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

        price = (
            abs(amount + fees) / quantity
            if amount is not None and quantity is not None
            else None
        )

        if (
            price is not None
            and self.price_foreign is not None
            and (self.currency_foreign == "GBP" or self.exchange_rate is not None)
        ):
            exchange_rate = self.exchange_rate or Decimal(1)
            calculated_price_foreign = price * exchange_rate
            discrepancy = self.price_foreign - calculated_price_foreign
            if abs(discrepancy) > Decimal("0.015"):
                LOGGER.warning(
                    "The Price per Share for this transaction after converting and "
                    "adding in the fees does not add up to the total amount. "
                    "You can fix the CSV by reviewing the transaction in the UI. "
                    "Discrepancy per Share: %.3f.",
                    float(discrepancy),
                )

        isin = row[Trading212Column.ISIN]
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
        )

    def __hash__(self) -> int:
        """Calculate hash."""
        return hash(self.transaction_id)


def validate_header(header: list[str], file: Path) -> None:
    """Check if header is valid. Not all columns exist in every export."""
    unknown = set(header) - COLUMN_SET
    if unknown:
        msg = f"Unknown column(s) {', '.join(sorted(unknown))}"
        raise ParsingError(file, msg)


def by_date_and_action(transaction: Trading212Transaction) -> tuple[datetime, bool]:
    """Sort by date and action type."""

    # If there's a deposit in the same second as a buy
    # (happens with the referral award at least)
    # we want to put the buy last to avoid negative balance errors
    return (transaction.datetime, transaction.action == ActionType.BUY)


def read_trading212_transactions(transactions_folder: Path) -> list[BrokerTransaction]:
    """Parse Trading 212 transactions from CSV file."""
    transactions = []
    for file in sorted(transactions_folder.glob("*.csv")):
        with file.open(encoding="utf-8") as csv_file:
            print(f"Parsing {file}...")
            lines = list(csv.reader(csv_file))
        if not lines:
            raise ParsingError(file, "Trading 212 CSV file is empty")
        header = lines[0]
        validate_header(header, file)
        lines = lines[1:]
        cur_transactions: list[Trading212Transaction] = []
        for index, row in enumerate(lines, start=2):
            try:
                cur_transactions.append(Trading212Transaction(header, row, file))
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file, str(err), row_index=index) from err
        if len(cur_transactions) == 0:
            LOGGER.warning("No transactions detected in file: %s", file)
        transactions += cur_transactions
    # Remove duplicates
    transactions = list(set(transactions))
    transactions.sort(key=by_date_and_action)
    return list(transactions)
