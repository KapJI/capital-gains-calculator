import csv
import datetime
from decimal import Decimal
from typing import Dict, List

from dates import date_to_index
from exceptions import ParsingError, UnexpectedColumnCountError
from model import BrokerTransaction, DateIndex


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


class SchwabTransaction(BrokerTransaction):
    def __init__(self, row: List[str], file: str):
        if len(row) != 9:
            raise UnexpectedColumnCountError(row, 9, file)
        if row[8] != "":
            raise ParsingError(file, "Column 9 should be empty")
        assert row[8] == "", "should be empty"
        as_of_str = " as of "
        if as_of_str in row[0]:
            index = row[0].find(as_of_str) + len(as_of_str)
            date_str = row[0][index:]
        else:
            date_str = row[0]
        date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        action = row[1]
        symbol = row[2]
        description = row[3]
        quantity = Decimal(row[4]) if row[4] != "" else None
        price = Decimal(row[5].replace("$", "")) if row[5] != "" else None
        fees = Decimal(row[6].replace("$", "")) if row[6] != "" else Decimal(0)
        amount = Decimal(row[7].replace("$", "")) if row[7] != "" else None
        super().__init__(
            date, action, symbol, description, quantity, price, fees, amount
        )


def read_broker_transactions(transactions_file: str) -> List[BrokerTransaction]:
    with open(transactions_file) as csv_file:
        lines = [line for line in csv.reader(csv_file)]
        lines = lines[2:-1]
        transactions = [SchwabTransaction(row, transactions_file) for row in lines]
        transactions.reverse()
        return list(transactions)


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
