"""Morgan Stanley parser.

Note, that I only had access to an Alphabet export. I have no idea how it looks like
for another company, or for a full profile.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

COLUMNS_RELEASE: Final[list[str]] = [
    "Vest Date",
    "Order Number",
    "Plan",
    "Type",
    "Status",
    "Price",
    "Quantity",
    "Net Cash Proceeds",
    "Net Share Proceeds",
    "Tax Payment Method",
]

COLUMNS_WITHDRAWAL: Final[list[str]] = [
    "Date",
    "Order Number",
    "Plan",
    "Type",
    "Order Status",
    "Price",
    "Quantity",
    "Net Amount",
    "Net Share Proceeds",
    "Tax Payment Method",
]

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


def _hacky_parse_decimal(decimal: str) -> Decimal:
    return Decimal(decimal.replace(",", ""))


def _init_from_release_report(row_raw: list[str], filename: str) -> BrokerTransaction:
    if len(COLUMNS_RELEASE) != len(row_raw):
        raise UnexpectedColumnCountError(row_raw, len(COLUMNS_RELEASE), filename)
    row = {col: row_raw[i] for i, col in enumerate(COLUMNS_RELEASE)}

    if row["Type"] != "Release":
        raise ParsingError(filename, f"Unknown type: {row_raw[3]}")

    if row["Status"] != "Complete" and row["Status"] != "Staged":
        raise ParsingError(filename, f"Unknown status: {row_raw[5]}")

    if row["Price"][0] != "$":
        raise ParsingError(filename, f"Unknown price currency: {row_raw[6]}")

    if row["Net Cash Proceeds"] != "$0.00":
        raise ParsingError(filename, f"Non-zero Net Cash Proceeds: {row_raw[8]}")

    if row["Plan"] not in KNOWN_SYMBOL_DICT:
        raise ParsingError(filename, f"Unknown plan: {row_raw[3]}")

    quantity = _hacky_parse_decimal(row["Net Share Proceeds"])
    price = _hacky_parse_decimal(row["Price"][1:])
    amount = quantity * price
    symbol = KNOWN_SYMBOL_DICT[row["Plan"]]
    symbol = TICKER_RENAMES.get(symbol, symbol)

    return BrokerTransaction(
        date=datetime.datetime.strptime(row["Vest Date"], "%d-%b-%Y").date(),
        action=ActionType.STOCK_ACTIVITY,
        symbol=symbol,
        description=row["Plan"],
        quantity=quantity,
        price=price,
        fees=Decimal(0),
        amount=amount,
        currency="USD",
        broker="Morgan Stanley",
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
    row_raw: list[str], filename: str
) -> BrokerTransaction | None:
    if _is_notice(row_raw):
        return None

    if len(COLUMNS_WITHDRAWAL) != len(row_raw):
        raise UnexpectedColumnCountError(row_raw, len(COLUMNS_WITHDRAWAL), filename)
    row = {col: row_raw[i] for i, col in enumerate(COLUMNS_WITHDRAWAL)}

    if row["Type"] != "Sale":
        raise ParsingError(filename, f"Unknown type: {row_raw[3]}")

    if row["Order Status"] != "Complete":
        raise ParsingError(filename, f"Unknown status: {row_raw[5]}")

    if row["Price"][0] != "$":
        raise ParsingError(filename, f"Unknown price currency: {row_raw[6]}")

    if row["Plan"] not in KNOWN_SYMBOL_DICT:
        raise ParsingError(filename, f"Unknown plan: {row_raw[3]}")

    quantity = -_hacky_parse_decimal(row["Quantity"])
    price = _hacky_parse_decimal(row["Price"][1:])
    amount = _hacky_parse_decimal(row["Net Amount"][1:])
    fees = quantity * price - amount

    if row["Plan"] == "Cash":
        action = ActionType.TRANSFER
        amount *= -1
    else:
        action = ActionType.SELL

    transaction = BrokerTransaction(
        date=datetime.datetime.strptime(row["Date"], "%d-%b-%Y").date(),
        action=action,
        symbol=KNOWN_SYMBOL_DICT[row["Plan"]],
        description=row["Plan"],
        quantity=quantity,
        price=price,
        fees=fees,
        amount=amount,
        currency="USD",
        broker="Morgan Stanley",
    )

    return _handle_stock_split(transaction)


def _validate_header(
    header: list[str], golden_header: list[str], filename: str
) -> None:
    """Check if header is valid."""
    if len(golden_header) != len(header):
        raise UnexpectedColumnCountError(header, len(golden_header), filename)
    for i, (expected, actual) in enumerate(zip(golden_header, header)):
        if expected != actual:
            msg = f"Expected column {i+1} to be {expected} but found {actual}"
            raise ParsingError(filename, msg)


def read_mssb_transactions(transactions_folder: str) -> list[BrokerTransaction]:
    """Parse Morgan Stanley transactions from CSV file."""
    transactions = []

    for file in Path(transactions_folder).glob("*.csv"):
        with Path(file).open(encoding="utf-8") as csv_file:
            if Path(file).name not in ["Withdrawals Report.csv", "Releases Report.csv"]:
                continue

            lines = list(csv.reader(csv_file))
            header = lines[0]
            lines = lines[1:]

            if Path(file).name == "Withdrawals Report.csv":
                _validate_header(header, COLUMNS_WITHDRAWAL, str(file))
                transactions += [
                    _init_from_withdrawal_report(row, str(file)) for row in lines
                ]
            else:
                _validate_header(header, COLUMNS_RELEASE, str(file))
                transactions += [
                    _init_from_release_report(row, str(file)) for row in lines
                ]

    return [transaction for transaction in transactions if transaction]
