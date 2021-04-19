"""Initial stock prices."""
from dataclasses import dataclass
import datetime
from decimal import Decimal
from typing import Dict

from .dates import date_to_index, is_date
from .exceptions import ExchangeRateMissingError
from .model import DateIndex


@dataclass
class InitialPrices:
    """Class to store initial stock prices."""

    initial_prices: Dict[DateIndex, Dict[str, Decimal]]

    def get(self, date: datetime.date, symbol: str) -> Decimal:
        """Get initial stock price at given date."""
        assert is_date(date)
        date_index = date_to_index(date)
        if (
            date_index not in self.initial_prices
            or symbol not in self.initial_prices[date_index]
        ):
            raise ExchangeRateMissingError(symbol, date)
        return self.initial_prices[date_index][symbol]
