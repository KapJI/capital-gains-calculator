"""Obtain current prices to calculate unrealized gains."""

from __future__ import annotations

import datetime
from decimal import Decimal

import yfinance as yf  # type: ignore

from .currency_converter import CurrencyConverter


class CurrentPriceFetcher:
    """Converter which holds rate history."""

    def __init__(
        self,
        converter: CurrencyConverter,
        current_prices_data: dict[str, Decimal | None] | None = None,
    ):
        """Load data from exchange_rates_file and optionally from initial_data."""
        self.current_prices_data = current_prices_data
        self.converter = converter

    def get_current_market_price(self, symbol: str) -> Decimal | None:
        """Given a symbol gets the current market price."""
        if self.current_prices_data is not None and symbol in self.current_prices_data:
            return self.current_prices_data[symbol]

        ticker = yf.Ticker(symbol).info
        if not ticker or "currentPrice" not in ticker:
            return None
        market_price_str = ticker["currentPrice"]
        market_price_usd = Decimal(format(market_price_str, ".15g"))
        return self.converter.to_gbp(
            market_price_usd, "USD", datetime.datetime.now().date()
        )
