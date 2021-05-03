"""Initial stock prices."""
from __future__ import annotations

from dataclasses import dataclass
import datetime
from decimal import Decimal

from .dates import is_date
from .exceptions import ExchangeRateMissingError


@dataclass
class InitialPrices:
    """Class to store initial stock prices."""

    initial_prices: dict[datetime.date, dict[str, Decimal]]

    def get(self, date: datetime.date, symbol: str) -> Decimal:
        """Get initial stock price at given date."""
        assert is_date(date)
        if date not in self.initial_prices or symbol not in self.initial_prices[date]:
            raise ExchangeRateMissingError(symbol, date)
        return self.initial_prices[date][symbol]
