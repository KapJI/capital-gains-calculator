"""Covert currency to GBP using price history."""
from __future__ import annotations

import datetime
from decimal import Decimal

from .dates import date_to_index, is_date
from .exceptions import ExchangeRateMissingError
from .model import BrokerTransaction


class CurrencyConverter:
    """Coverter which holds price history."""

    def __init__(self, gbp_history: dict[int, Decimal]):
        """Create from GBP/USD price history."""
        self.gbp_history = gbp_history

    def usd_to_gbp_price(self, date: datetime.date) -> Decimal:
        """Get GBP/USD price at given date."""
        assert is_date(date)
        # Set day to 1 to get monthly price
        index = date_to_index(date.replace(day=1))
        if index not in self.gbp_history:
            raise ExchangeRateMissingError("USD", date)
        return self.gbp_history[index]

    def to_gbp(self, amount: Decimal, currency: str, date: datetime.date) -> Decimal:
        """Convert amount from given currency to GBP."""
        if currency == "USD":
            return amount / self.usd_to_gbp_price(date)
        if currency == "GBP":
            return amount
        raise ExchangeRateMissingError(currency, date)

    def to_gbp_for(self, amount: Decimal, transaction: BrokerTransaction) -> Decimal:
        """Convert amount from transaction currency to GBP."""
        return self.to_gbp(amount, transaction.currency, transaction.date)
