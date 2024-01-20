"""Charles Schwab parser."""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction


def action_from_str(label: str) -> ActionType:
    """Convert string label to ActionType."""
    try:
        return ActionType[label.upper()]
    except KeyError as exc:
        raise ParsingError("raw transactions", f"Unknown action: {label}") from exc


class RawTransaction(BrokerTransaction):
    """
    Represent single raw transaction.

    Expected format is:
    date (YYYY-MM-DD);action;symbol?;quantity;price;fees;currency
    """

    def __init__(
        self,
        row: list[str],
        file: str,
    ):
        """Create transaction from CSV row."""
        if len(row) != 7:
            raise UnexpectedColumnCountError(row, 7, file)

        date_str = row[0]
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        action = action_from_str(row[1])
        symbol = row[2] if row[2] != "" else None

        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        quantity = Decimal(row[3].replace(",", "")) if row[4] != "" else None
        price = Decimal(row[4]) if row[4] != "" else None
        fees = Decimal(row[5]) if row[5] != "" else Decimal(0)

        if price is not None and quantity is not None:
            amount = price * quantity

            if action is ActionType.BUY:
                amount = -abs(amount)
            amount -= fees
        else:
            amount = None

        currency = row[6]
        broker = "Unknown"
        super().__init__(
            date,
            action,
            symbol,
            "",
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
        )


def read_raw_transactions(transactions_file: str) -> list[BrokerTransaction]:
    """Read Raw transactions from file."""
    try:
        with Path(transactions_file).open(encoding="utf-8") as csv_file:
            lines = list(csv.reader(csv_file))
    except FileNotFoundError:
        print(f"WARNING: Couldn't locate Raw transactions file({transactions_file})")
        return []

    return [RawTransaction(row, transactions_file) for row in lines]
