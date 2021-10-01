"""Trading 212 parser."""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

COLUMNS: Final[list[str]] = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result (GBP)",
    "Total (GBP)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Charge amount (GBP)",
    "Transaction fee (GBP)",
    "Finra fee (GBP)",
    "Notes",
    "ID",
]


def decimal_or_none(val: str) -> Decimal | None:
    """Convert value to Decimal."""
    return Decimal(val) if val not in ["", "Not available"] else None


def action_from_str(label: str, filename: str) -> ActionType:
    """Convert label to ActionType."""
    if label in [
        "Market buy",
        "Limit buy",
    ]:
        return ActionType.BUY

    if label in [
        "Market sell",
        "Limit sell",
    ]:
        return ActionType.SELL

    if label in [
        "Deposit",
        "Withdrawal",
    ]:
        return ActionType.TRANSFER

    if label in ["Dividend (Ordinary)"]:
        return ActionType.DIVIDEND

    raise ParsingError(filename, f"Unknown action: {label}")


class Trading212Transaction(BrokerTransaction):
    """Represent single Trading 212 transaction."""

    def __init__(self, row_raw: list[str], filename: str):
        """Create transaction from CSV row."""
        if len(COLUMNS) != len(row_raw):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), filename)
        row = {col: row_raw[i] for i, col in enumerate(COLUMNS)}
        time_str = row["Time"]
        self.datetime = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        date = self.datetime.date()
        self.raw_action = row["Action"]
        action = action_from_str(self.raw_action, filename)
        symbol = row["Ticker"] if row["Ticker"] != "" else None
        description = row["Name"]
        quantity = decimal_or_none(row["No. of shares"])
        self.price_foreign = decimal_or_none(row["Price / share"])
        self.currency_foreign = row["Currency (Price / share)"]
        self.exchange_rate = decimal_or_none(row["Exchange rate"])
        self.transaction_fee = decimal_or_none(row["Transaction fee (GBP)"])
        self.finra_fee = decimal_or_none(row["Finra fee (GBP)"])
        fees = (self.transaction_fee or Decimal(0)) + (self.finra_fee or Decimal(0))
        amount = decimal_or_none(row["Total (GBP)"])
        price = (
            abs(amount / quantity)
            if amount is not None and quantity is not None
            else None
        )
        if amount is not None:
            if action == ActionType.BUY or self.raw_action == "Withdrawal":
                amount *= -1
            amount -= fees
        self.isin = row["ISIN"]
        self.transaction_id = row["ID"]
        self.notes = row["Notes"]
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
            "GBP",
            broker,
        )

    def __eq__(self, other: object) -> bool:
        """Compare transactions by ID."""
        if not isinstance(other, Trading212Transaction):
            raise NotImplementedError()
        return self.transaction_id == other.transaction_id

    def __hash__(self) -> int:
        """Calculate hash."""
        return hash(self.transaction_id)


def validate_header(header: list[str], filename: str) -> None:
    """Check if header is valid."""
    if len(COLUMNS) != len(header):
        raise UnexpectedColumnCountError(header, len(COLUMNS), filename)
    for i, (expected, actual) in enumerate(zip(COLUMNS, header)):
        if expected != actual:
            msg = f"Expected column {i+1} to be {expected} but found {actual}"
            raise ParsingError(filename, msg)


def by_date_and_action(transaction: Trading212Transaction) -> tuple[datetime, bool]:
    """Sort by date and action type."""

    # If there's a deposit in the same second as a buy
    # (happens with the referral award at least)
    # we want to put the buy last to avoid negative balance errors
    return (transaction.datetime, transaction.action == ActionType.BUY)


def read_trading212_transactions(transactions_folder: str) -> list[BrokerTransaction]:
    """Parse Trading 212 transactions from CSV file."""
    transactions = []
    for file in Path(transactions_folder).glob("*.csv"):
        with Path(file).open(encoding="utf-8") as csv_file:
            print(f"Parsing {file}")
            lines = list(csv.reader(csv_file))
            validate_header(lines[0], str(file))
            lines = lines[1:]
            cur_transactions = [Trading212Transaction(row, str(file)) for row in lines]
            if len(cur_transactions) == 0:
                print(f"WARNING: no transactions detected in file {file}")
            transactions += cur_transactions
    # remove duplicates
    transactions = list(set(transactions))
    transactions.sort(key=by_date_and_action)
    return list(transactions)
