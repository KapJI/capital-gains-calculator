import datetime
from decimal import Decimal
from typing import Dict

from .dates import date_to_index, is_date
from .exceptions import ExchangeRateMissingError
from .model import DateIndex


class InitialPrices:
    def __init__(self, initial_prices: Dict[DateIndex, Dict[str, Decimal]]):
        self.initial_prices = initial_prices

    def get(self, date: datetime.date, symbol: str) -> Decimal:
        assert is_date(date)
        date_index = date_to_index(date)
        if (
            date_index in self.initial_prices
            and symbol in self.initial_prices[date_index]
        ):
            return self.initial_prices[date_index][symbol]
        else:
            raise ExchangeRateMissingError(symbol, date)
