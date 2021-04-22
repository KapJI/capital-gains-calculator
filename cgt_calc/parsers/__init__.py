"""Parse input files."""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal
import importlib.resources
import operator
from pathlib import Path

from cgt_calc.const import DEFAULT_GBP_HISTORY_FILE, DEFAULT_INITIAL_PRICES_FILE
from cgt_calc.dates import date_to_index
from cgt_calc.exceptions import UnexpectedColumnCountError
from cgt_calc.model import BrokerTransaction, DateIndex
from cgt_calc.resources import RESOURCES_PACKAGE

from .schwab import read_schwab_transactions
from .trading212 import read_trading212_transactions


class InitialPricesEntry:
    """Entry from initial stock prices file."""

    def __init__(self, row: list[str], file: str):
        """Create entry from CSV row."""
        if len(row) != 3:
            raise UnexpectedColumnCountError(row, 3, file)
        # date,symbol,price
        self.date = self._parse_date(row[0])
        self.symbol = row[1]
        self.price = Decimal(row[2])

    @staticmethod
    def _parse_date(date_str: str) -> datetime.date:
        """Parse date from string."""
        return datetime.datetime.strptime(date_str, "%b %d, %Y").date()

    def __str__(self) -> str:
        """Return string representation."""
        return f"date: {self.date}, symbol: {self.symbol}, price: {self.price}"


def read_broker_transactions(
    schwab_transactions_file: str | None,
    trading212_transactions_folder: str | None,
) -> list[BrokerTransaction]:
    """Read transactions for all brokers."""
    transactions = []
    if schwab_transactions_file is not None:
        transactions += read_schwab_transactions(schwab_transactions_file)
    else:
        print("WARNING: No schwab file provided")
    if trading212_transactions_folder is not None:
        transactions += read_trading212_transactions(trading212_transactions_folder)
    else:
        print("WARNING: No trading212 folder provided")
    transactions.sort(key=operator.attrgetter("date"))
    return transactions


def read_gbp_prices_history(gbp_history_file: str | None) -> dict[int, Decimal]:
    """Read GBP/USD history from CSV file."""
    gbp_history: dict[int, Decimal] = {}
    if gbp_history_file is None:
        csv_file = importlib.resources.open_text(
            RESOURCES_PACKAGE, DEFAULT_GBP_HISTORY_FILE
        )
        lines = list(csv.reader(csv_file))
        csv_file.close()
    else:
        with Path(gbp_history_file).open() as csv_file:
            lines = list(csv.reader(csv_file))
    lines = lines[1:]
    for row in lines:
        if len(row) != 2:
            raise UnexpectedColumnCountError(row, 2, gbp_history_file or "default")
        price_date = datetime.datetime.strptime(row[0], "%m/%Y").date()
        gbp_history[date_to_index(price_date)] = Decimal(row[1])
    return gbp_history


def read_initial_prices(
    initial_prices_file: str | None,
) -> dict[DateIndex, dict[str, Decimal]]:
    """Read initial stock prices from CSV file."""
    initial_prices: dict[DateIndex, dict[str, Decimal]] = {}
    if initial_prices_file is None:
        csv_file = importlib.resources.open_text(
            RESOURCES_PACKAGE, DEFAULT_INITIAL_PRICES_FILE
        )
        lines = list(csv.reader(csv_file))
        csv_file.close()
    else:
        with Path(initial_prices_file).open() as csv_file:
            lines = list(csv.reader(csv_file))
    lines = lines[1:]
    for row in lines:
        entry = InitialPricesEntry(row, initial_prices_file or "default")
        date_index = date_to_index(entry.date)
        if date_index not in initial_prices:
            initial_prices[date_index] = {}
        initial_prices[date_index][entry.symbol] = entry.price
    return initial_prices
