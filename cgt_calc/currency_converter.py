import datetime
from decimal import Decimal
from typing import Dict

from .dates import date_to_index, is_date
from .exceptions import ExchangeRateMissingError
from .model import BrokerTransaction


class CurrencyConverter:
    def __init__(self, gbp_history: Dict[int, Decimal]):
        self.gbp_history = gbp_history

    def usd_to_gbp_price(self, date: datetime.date) -> Decimal:
        assert is_date(date)
        # Set day to 1 to get monthly price
        index = date_to_index(date.replace(day=1))
        if index in self.gbp_history:
            return self.gbp_history[index]
        else:
            raise ExchangeRateMissingError("USD", date)

    def to_gbp(self, amount: Decimal, currency: str, date: datetime.date) -> Decimal:
        if currency == "USD":
            return amount / self.usd_to_gbp_price(date)
        elif currency == "GBP":
            return amount
        else:
            raise ExchangeRateMissingError(currency, date)

    def to_gbp_for(self, amount: Decimal, transaction: BrokerTransaction) -> Decimal:
        return self.to_gbp(amount, transaction.currency, transaction.date)
