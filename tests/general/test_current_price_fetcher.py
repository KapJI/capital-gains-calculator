"""Test CurrentPriceFetcher."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher

if TYPE_CHECKING:
    import pytest


class FakeTicker:
    """Stand-in for yf.Ticker that returns a canned info dict."""

    def __init__(self, info: dict[str, float | str]) -> None:
        """Store the info dict to return."""
        self.info = info


def _fetcher() -> CurrentPriceFetcher:
    today = datetime.datetime.now().date()
    converter = CurrencyConverter(
        None, {today: {"USD": Decimal("1.25"), "EUR": Decimal("1.15")}}
    )
    return CurrentPriceFetcher(converter)


def test_uses_current_price_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """currentPrice, when present, is used as-is."""
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0}),
    )
    price = _fetcher().get_current_market_price("AAPL")
    assert price == Decimal("100.0") / Decimal("1.25")


def test_falls_back_to_regular_market_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """ETFs like VTI lack currentPrice but carry regularMarketPrice and navPrice.

    See https://github.com/KapJI/capital-gains-calculator/issues/801.
    """
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"regularMarketPrice": 366.79, "navPrice": 366.74}),
    )
    price = _fetcher().get_current_market_price("VTI")
    assert price == Decimal("366.79") / Decimal("1.25")


def test_falls_back_to_nav_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some tickers only carry navPrice, with no currentPrice or regularMarketPrice."""
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"navPrice": 42.5}),
    )
    price = _fetcher().get_current_market_price("BND")
    assert price == Decimal("42.5") / Decimal("1.25")


def test_returns_none_when_no_price_field_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No known price field means the price is genuinely unavailable."""
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"someOtherField": 1}),
    )
    assert _fetcher().get_current_market_price("XYZ") is None


def test_returns_none_when_ticker_info_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty info dict (e.g. an unknown symbol) yields no price."""
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({}),
    )
    assert _fetcher().get_current_market_price("UNKNOWN") is None


def test_gbp_pence_quoted_ticker_is_divided_by_100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LSE-listed tickers are quoted in pence ("GBp"), not pounds or USD.

    yfinance's "GBp" is not a real ISO currency code, so it must be
    converted directly to GBP rather than looked up via CurrencyConverter.
    """
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0, "currency": "GBp"}),
    )
    price = _fetcher().get_current_market_price("VOD.L")
    assert price == Decimal("1.0")


def test_eur_quoted_ticker_uses_eur_rate_not_usd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A EUR-quoted ticker should be converted using the EUR rate, not USD."""
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0, "currency": "EUR"}),
    )
    price = _fetcher().get_current_market_price("MC.PA")
    assert price == Decimal("100.0") / Decimal("1.15")


def test_defaults_to_usd_when_currency_field_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When "currency" is missing from ticker info, fall back to USD."""
    monkeypatch.setattr(
        "cgt_calc.current_price_fetcher.yf.Ticker",
        lambda symbol: FakeTicker({"currentPrice": 100.0}),
    )
    price = _fetcher().get_current_market_price("AAPL")
    assert price == Decimal("100.0") / Decimal("1.25")
