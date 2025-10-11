"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
import logging
from pathlib import Path
from typing import Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

CSV_COLUMNS_NUM: Final = 7
LOGGER = logging.getLogger(__name__)


def action_from_str(label: str) -> ActionType:
    """Convert string label to ActionType."""
    try:
        return ActionType[label.upper()]
    except KeyError as exc:
        raise ParsingError("raw transactions", f"Unknown action: {label}") from exc


class RawTransaction(BrokerTransaction):
    """Represents a single raw transaction.

    Example format:
    2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD
    2022-11-14,SELL,META,19,116.00,0.05,USD
    2022-08-15,BUY,META,105,180.50,0.00,USD
    2022-07-26,DIVIDEND,OTGLY,305,0.031737,0.0,USD
    2022-06-06,STOCK_SPLIT,AMZN,209,0.00,0.00,USD

    See tests/test_data/raw/test_data.csv for a sample file showing the expected format.
    """

    def __init__(
        self,
        row: list[str],
        file: str,
    ):
        """Create transaction from CSV row."""
        if len(row) != CSV_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, CSV_COLUMNS_NUM, file)

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
            print(f"Parsing {transactions_file}...")
            lines = list(csv.reader(csv_file))
    except FileNotFoundError:
        LOGGER.warning("Couldn't locate RAW transactions file: %s", transactions_file)
        return []

    return [RawTransaction(row, transactions_file) for row in lines]
