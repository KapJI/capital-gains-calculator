"""Obtain current prices to calculate unrealized gains."""

from __future__ import annotations

from contextlib import suppress
import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import yfinance as yf  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from .currency_converter import CurrencyConverter


class CurrentPriceFetcher:
    """Converter which holds rate history."""

    def __init__(
        self,
        converter: CurrencyConverter,
        current_prices_data: dict[str, Decimal | None] | None = None,
        historical_prices_data: dict[str, dict[datetime.date, Decimal]] | None = None,
    ):
        """Load data from exchange_rates_file and optionally from initial_data."""
        self.current_prices_data = current_prices_data
        self.historical_prices_data = historical_prices_data or {}
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

    def get_closing_price(self, symbol: str, date: datetime.date) -> Decimal:
        """Get the price of the share on closing time."""
        with suppress(KeyError):
            return self.historical_prices_data[symbol][date]

        prices = yf.Ticker(symbol).history(
            period="1d",
            interval="1d",
            start=date.strftime("%Y-%m-%d"),
            end=(date + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
        )
        closing_price = prices.iloc[0]["Close"]
        market_price_usd = Decimal(format(closing_price, ".15g"))
        return self.converter.to_gbp(
            market_price_usd, "USD", datetime.datetime.now().date()
        )
