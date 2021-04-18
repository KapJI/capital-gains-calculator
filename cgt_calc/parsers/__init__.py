import csv
import datetime
from decimal import Decimal
import operator
from typing import Dict, List, Optional

from cgt_calc.dates import date_to_index
from cgt_calc.exceptions import UnexpectedColumnCountError
from cgt_calc.model import BrokerTransaction, DateIndex

from .schwab import read_schwab_transactions
from .trading212 import read_trading212_transactions


class InitialPricesEntry:
    def __init__(self, row: List[str], file: str):
        if len(row) != 3:
            raise UnexpectedColumnCountError(row, 3, file)
        # date,symbol,price
        self.date = self._parse_date(row[0])
        self.symbol = row[1]
        self.price = Decimal(row[2])

    @staticmethod
    def _parse_date(date_str: str) -> datetime.date:
        return datetime.datetime.strptime(date_str, "%b %d, %Y").date()

    def __str__(self) -> str:
        return f"date: {self.date}, symbol: {self.symbol}, price: {self.price}"


def read_broker_transactions(
    schwab_transactions_file: Optional[str],
    trading212_transactions_folder: Optional[str],
) -> List[BrokerTransaction]:
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


def read_gbp_prices_history(gbp_history_file: str) -> Dict[int, Decimal]:
    gbp_history: Dict[int, Decimal] = {}
    with open(gbp_history_file) as csv_file:
        lines = [line for line in csv.reader(csv_file)]
        lines = lines[1:]
        for row in lines:
            if len(row) != 2:
                raise UnexpectedColumnCountError(row, 2, gbp_history_file)
            price_date = datetime.datetime.strptime(row[0], "%m/%Y").date()
            gbp_history[date_to_index(price_date)] = Decimal(row[1])
    return gbp_history


def read_initial_prices(
    initial_prices_file: str,
) -> Dict[DateIndex, Dict[str, Decimal]]:
    initial_prices: Dict[DateIndex, Dict[str, Decimal]] = {}
    with open(initial_prices_file) as csv_file:
        lines = [line for line in csv.reader(csv_file)]
        lines = lines[1:]
        for row in lines:
            entry = InitialPricesEntry(row, initial_prices_file)
            date_index = date_to_index(entry.date)
            if date_index not in initial_prices:
                initial_prices[date_index] = {}
            initial_prices[date_index][entry.symbol] = entry.price
    return initial_prices
