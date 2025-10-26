"""Morgan Stanley parser.

Note, that I only had access to an Alphabet export. I have no idea how it looks like
for another company, or for a full profile.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

if TYPE_CHECKING:
    from pathlib import Path

BROKER_NAME: Final = "Morgan Stanley"
WITHDRAWALS_REPORT_FILENAME: Final = "Withdrawals Report.csv"
RELEASES_REPORT_FILENAME: Final = "Releases Report.csv"
LOGGER = logging.getLogger(__name__)


class ReleaseColumn(StrEnum):
    """Column names for release reports."""

    VEST_DATE = "Vest Date"
    ORDER_NUMBER = "Order Number"
    PLAN = "Plan"
    TYPE = "Type"
    STATUS = "Status"
    PRICE = "Price"
    QUANTITY = "Quantity"
    NET_CASH_PROCEEDS = "Net Cash Proceeds"
    NET_SHARE_PROCEEDS = "Net Share Proceeds"
    TAX_PAYMENT_METHOD = "Tax Payment Method"


class WithdrawalColumn(StrEnum):
    """Column names for withdrawal reports."""

    EXECUTION_DATE = "Execution Date"
    ORDER_NUMBER = "Order Number"
    PLAN = "Plan"
    TYPE = "Type"
    ORDER_STATUS = "Order Status"
    PRICE = "Price"
    QUANTITY = "Quantity"
    NET_AMOUNT = "Net Amount"
    NET_SHARE_PROCEEDS = "Net Share Proceeds"
    TAX_PAYMENT_METHOD = "Tax Payment Method"


COLUMNS_RELEASE: Final[list[str]] = [column.value for column in ReleaseColumn]
COLUMNS_WITHDRAWAL: Final[list[str]] = [column.value for column in WithdrawalColumn]

# These can be potentially wired through as a flag
KNOWN_SYMBOL_DICT: Final[dict[str, str]] = {
    "GSU Class C": "GOOG",
    "Cash": "USD",
}


@dataclass
class StockSplit:
    """Info about stock split."""

    symbol: str
    date: datetime.date
    factor: int


STOCK_SPLIT_INFO = [
    StockSplit(symbol="GOOG", date=datetime.datetime(2022, 6, 15).date(), factor=20),
]


def _parse_decimal(row: dict[str, str], column: StrEnum) -> Decimal:
    """Parse a decimal from the given row, annotating errors with column context."""

    value = row[column]
    cleaned = value.replace(",", "").replace("$", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as err:
        raise ValueError(
            f"Invalid decimal in column '{column.value}': {value!r}"
        ) from err


def _init_from_release_report(row_raw: list[str], file: Path) -> BrokerTransaction:
    if len(COLUMNS_RELEASE) != len(row_raw):
        raise UnexpectedColumnCountError(row_raw, len(COLUMNS_RELEASE), file)
    row = dict(zip(COLUMNS_RELEASE, row_raw, strict=False))

    plan = row[ReleaseColumn.PLAN]

    if row[ReleaseColumn.TYPE] != "Release":
        raise ParsingError(file, f"Unknown type: {row[ReleaseColumn.TYPE]}")

    if row[ReleaseColumn.STATUS] not in {"Complete", "Staged"}:
        raise ParsingError(file, f"Unknown status: {row[ReleaseColumn.STATUS]}")

    price_raw = row[ReleaseColumn.PRICE]
    if not price_raw or not price_raw.startswith("$"):
        raise ParsingError(file, f"Unknown price currency: {price_raw}")

    if row[ReleaseColumn.NET_CASH_PROCEEDS] != "$0.00":
        raise ParsingError(
            file,
            f"Non-zero Net Cash Proceeds: {row[ReleaseColumn.NET_CASH_PROCEEDS]}",
        )

    if plan not in KNOWN_SYMBOL_DICT:
        raise ParsingError(file, f"Unknown plan: {plan}")

    quantity = _parse_decimal(row, ReleaseColumn.NET_SHARE_PROCEEDS)
    price = _parse_decimal(row, ReleaseColumn.PRICE)
    amount = quantity * price
    symbol = KNOWN_SYMBOL_DICT[plan]
    symbol = TICKER_RENAMES.get(symbol, symbol)

    return BrokerTransaction(
        date=datetime.datetime.strptime(
            row[ReleaseColumn.VEST_DATE], "%d-%b-%Y"
        ).date(),
        action=ActionType.STOCK_ACTIVITY,
        symbol=symbol,
        description=plan,
        quantity=quantity,
        price=price,
        fees=Decimal(0),
        amount=amount,
        currency="USD",
        broker=BROKER_NAME,
    )


# Morgan Stanley decided to put a notice in the end of the withdrawal report that looks
# like that:
# "Please note that any Alphabet share sales, transfers, or deposits that occurred on
# or prior to the July 15, 2022 stock split are reflected in pre-split. Any sales,
# transfers, or deposits that occurred after July 15, 2022 are in post-split values.
# For GSU vests, your activity is displayed in post-split values."
# It makes sense, but it totally breaks the CSV parsing
def _is_notice(row: list[str]) -> bool:
    return row[0][:11] == "Please note"


def _handle_stock_split(transaction: BrokerTransaction) -> BrokerTransaction:
    for split in STOCK_SPLIT_INFO:
        if (
            transaction.symbol == split.symbol
            and transaction.action == ActionType.SELL
            and transaction.date < split.date
        ):
            if transaction.quantity:
                transaction.quantity *= split.factor
            if transaction.price:
                transaction.price /= split.factor

    return transaction


def _init_from_withdrawal_report(
    row_raw: list[str], file: Path
) -> BrokerTransaction | None:
    if _is_notice(row_raw):
        return None

    if len(COLUMNS_WITHDRAWAL) != len(row_raw):
        raise UnexpectedColumnCountError(row_raw, len(COLUMNS_WITHDRAWAL), file)
    row = dict(zip(COLUMNS_WITHDRAWAL, row_raw, strict=False))

    plan = row[WithdrawalColumn.PLAN]

    if row[WithdrawalColumn.TYPE] != "Sale":
        raise ParsingError(file, f"Unknown type: {row[WithdrawalColumn.TYPE]}")

    if row[WithdrawalColumn.ORDER_STATUS] != "Complete":
        raise ParsingError(
            file, f"Unknown status: {row[WithdrawalColumn.ORDER_STATUS]}"
        )

    price_raw = row[WithdrawalColumn.PRICE]
    if not price_raw or not price_raw.startswith("$"):
        raise ParsingError(file, f"Unknown price currency: {price_raw}")

    if plan not in KNOWN_SYMBOL_DICT:
        raise ParsingError(file, f"Unknown plan: {plan}")

    quantity = -_parse_decimal(row, WithdrawalColumn.QUANTITY)
    price = _parse_decimal(row, WithdrawalColumn.PRICE)
    amount = _parse_decimal(row, WithdrawalColumn.NET_AMOUNT)
    fees = quantity * price - amount

    if plan == "Cash":
        action = ActionType.TRANSFER
        amount *= -1
    else:
        action = ActionType.SELL

    transaction = BrokerTransaction(
        date=datetime.datetime.strptime(
            row[WithdrawalColumn.EXECUTION_DATE], "%d-%b-%Y"
        ).date(),
        action=action,
        symbol=KNOWN_SYMBOL_DICT[plan],
        description=plan,
        quantity=quantity,
        price=price,
        fees=fees,
        amount=amount,
        currency="USD",
        broker=BROKER_NAME,
    )

    return _handle_stock_split(transaction)


def _validate_header(header: list[str], golden_header: list[str], file: Path) -> None:
    """Check if header is valid."""
    if len(golden_header) != len(header):
        raise UnexpectedColumnCountError(header, len(golden_header), file)
    for i, (expected, actual) in enumerate(zip(golden_header, header, strict=True)):
        if expected != actual:
            msg = f"Expected column {i + 1} to be {expected} but found {actual}"
            raise ParsingError(file, msg)


def read_mssb_transactions(transactions_folder: Path) -> list[BrokerTransaction]:
    """Parse Morgan Stanley transactions from CSV file."""
    transactions: list[BrokerTransaction] = []

    for file in sorted(transactions_folder.glob("*.csv")):
        with file.open(encoding="utf-8") as csv_file:
            if file.name not in [
                WITHDRAWALS_REPORT_FILENAME,
                RELEASES_REPORT_FILENAME,
            ]:
                continue

            print(f"Parsing {file}...")
            lines = list(csv.reader(csv_file))
            if not lines:
                raise ParsingError(file, "Morgan Stanley CSV file is empty")
            header = lines[0]
            lines = lines[1:]

            if file.name == WITHDRAWALS_REPORT_FILENAME:
                _validate_header(header, COLUMNS_WITHDRAWAL, file)
                for index, row in enumerate(lines, start=2):
                    try:
                        transaction = _init_from_withdrawal_report(row, file)
                    except ParsingError as err:
                        err.add_row_context(index)
                        raise
                    except ValueError as err:
                        raise ParsingError(file, str(err), row_index=index) from err
                    if transaction:
                        transactions.append(transaction)
            else:
                _validate_header(header, COLUMNS_RELEASE, file)
                for index, row in enumerate(lines, start=2):
                    try:
                        transaction = _init_from_release_report(row, file)
                    except ParsingError as err:
                        err.add_row_context(index)
                        raise
                    except ValueError as err:
                        raise ParsingError(file, str(err), row_index=index) from err
                    transactions.append(transaction)

    if len(transactions) == 0:
        LOGGER.warning("No transactions detected in directory %s", transactions_folder)
    return transactions
