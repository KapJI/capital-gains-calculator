"""Charles Schwab parser."""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction


def action_from_str(label: str) -> ActionType:
    """Convert string label to ActionType."""
    if label == "Buy":
        return ActionType.BUY

    if label == "Sell":
        return ActionType.SELL

    if label in [
        "MoneyLink Transfer",
        "Misc Cash Entry",
        "Service Fee",
        "Wire Funds",
        "Funds Received",
        "Journal",
        "Cash In Lieu",
    ]:
        return ActionType.TRANSFER

    if label == "Stock Plan Activity":
        return ActionType.STOCK_ACTIVITY

    if label in ["Qualified Dividend", "Cash Dividend"]:
        return ActionType.DIVIDEND

    if label in ["NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"]:
        return ActionType.TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in ["Adjustment", "IRS Withhold Adj"]:
        return ActionType.ADJUSTMENT

    if label in ["Short Term Cap Gain", "Long Term Cap Gain"]:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label == "Credit Interest":
        return ActionType.INTEREST

    raise ParsingError("schwab transactions", f"Unknown action: {label}")


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(self, row: list[str], file: str):
        """Create transaction from CSV row."""
        if len(row) != 9:
            raise UnexpectedColumnCountError(row, 9, file)
        if row[8] != "":
            raise ParsingError(file, "Column 9 should be empty")
        as_of_str = " as of "
        if as_of_str in row[0]:
            index = row[0].find(as_of_str) + len(as_of_str)
            date_str = row[0][index:]
        else:
            date_str = row[0]
        date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        self.raw_action = row[1]
        action = action_from_str(self.raw_action)
        symbol = row[2] if row[2] != "" else None
        description = row[3]
        quantity = Decimal(row[4]) if row[4] != "" else None
        price = Decimal(row[5].replace("$", "")) if row[5] != "" else None
        fees = Decimal(row[6].replace("$", "")) if row[6] != "" else Decimal(0)
        amount = Decimal(row[7].replace("$", "")) if row[7] != "" else None
        currency = "USD"
        broker = "Charles Schwab"
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
        )


def read_schwab_transactions(transactions_file: str) -> list[BrokerTransaction]:
    """Read Schwab transactions from file."""
    try:
        with Path(transactions_file).open(encoding="utf-8") as csv_file:
            lines = list(csv.reader(csv_file))
            lines = lines[2:-1]
            transactions = [SchwabTransaction(row, transactions_file) for row in lines]
            transactions.reverse()
            return list(transactions)
    except FileNotFoundError:
        print(f"WARNING: Couldn't locate Schwab transactions file({transactions_file})")
        return []
