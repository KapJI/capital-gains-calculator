"""Tests for currency converter exchange rate loading."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, NoReturn

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.exceptions import ExternalApiError, ParsingError

if TYPE_CHECKING:
    from pathlib import Path


def test_read_exchange_rates_successfully(tmp_path: Path) -> None:
    """Loading a populated rates file captures every row."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,1.25\n2024-02-01,EUR,1.10\n",
        encoding="utf8",
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache

    january_rates = cache[datetime.date(2024, 1, 1)]
    february_rates = cache[datetime.date(2024, 2, 1)]
    assert january_rates == {"USD": Decimal("1.25")}
    assert february_rates == {"EUR": Decimal("1.10")}


def test_read_exchange_rates_handles_empty_file(tmp_path: Path) -> None:
    """Empty rates files produce an empty cache without failing."""
    rates_file = tmp_path / "empty.csv"
    # create an empty file
    rates_file.touch()

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache
    assert cache == {}


def test_read_exchange_rates_skips_blank_rows(tmp_path: Path) -> None:
    """Blank rows are ignored rather than causing parsing errors."""
    rates_file = tmp_path / "blank.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,1.25\n,,\n2024-02-01,EUR,1.10\n",
        encoding="utf8",
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache

    january = datetime.date(2024, 1, 1)
    february = datetime.date(2024, 2, 1)
    assert cache[january] == {"USD": Decimal("1.25")}
    assert cache[february] == {"EUR": Decimal("1.10")}


def test_read_exchange_rates_raises_on_invalid_date(tmp_path: Path) -> None:
    """Invalid month values raise a parsing error with context."""
    rates_file = tmp_path / "invalid_date.csv"
    rates_file.write_text(
        "month,currency,rate\n2024/01/01,USD,1.25\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match="Invalid date '2024/01/01'"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_raises_on_invalid_rate(tmp_path: Path) -> None:
    """Non-decimal rate values raise a parsing error."""
    rates_file = tmp_path / "invalid_rate.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,one.two\n",
        encoding="utf8",
    )

    with pytest.raises(
        ParsingError,
        match=re.escape("Invalid rate 'one.two'"),
    ):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_raises_on_duplicate_currency(tmp_path: Path) -> None:
    """Duplicate currency entries for the same month raise a parsing error."""
    rates_file = tmp_path / "duplicate.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,1.25\n2024-01-01,USD,1.30\n",
        encoding="utf8",
    )

    with pytest.raises(
        ParsingError,
        match="Duplicate currency entry for USD on 2024-01-01",
    ):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_skips_comment_lines(tmp_path: Path) -> None:
    """Lines starting with # are ignored rather than causing parsing errors."""
    rates_file = tmp_path / "comments.csv"
    rates_file.write_text(
        "# This is a comment header\n# another comment line\nmonth,currency,rate\n2024-01-01,USD,1.25\n# middle comment\n2024-02-01,EUR,1.10\n",
        encoding="utf8",
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)
    cache = converter.cache

    january = datetime.date(2024, 1, 1)
    february = datetime.date(2024, 2, 1)
    assert cache[january] == {"USD": Decimal("1.25")}
    assert cache[february] == {"EUR": Decimal("1.10")}


class OfflineSession:
    """Session stub that fails every request."""

    def get(self, url: str, timeout: int) -> NoReturn:
        """Simulate a network failure."""
        raise requests_exceptions.ConnectionError(f"offline: {url}")


def test_hmrc_fetch_announces_itself(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fetching rates from HMRC logs a progress line before the request."""
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", OfflineSession())

    with caplog.at_level(logging.INFO), pytest.raises(ExternalApiError):
        converter.currency_to_gbp_rate("USD", datetime.date(2021, 5, 10))

    assert "Fetching HMRC exchange rates for 2021-05..." in caplog.text


class CannedResponse:
    """Minimal stand-in for a successful requests.Response."""

    ok = True

    def __init__(self, text: str) -> None:
        """Store the body to return."""
        self.text = text


class CannedSession:
    """Session stub that returns the same response for every request."""

    def __init__(self, response: CannedResponse) -> None:
        """Store the response to return."""
        self._response = response

    def get(self, url: str, timeout: int) -> CannedResponse:
        """Return the canned response."""
        return self._response


def test_hmrc_response_missing_rate_element_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row without a rateNew element raises ExternalApiError."""
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate><currencyCode>USD</currencyCode></exchangeRate>"
        "</exchangeRateMonthList>"
    )
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", CannedSession(CannedResponse(xml)))

    with pytest.raises(ExternalApiError, match="missing expected currency data"):
        converter.currency_to_gbp_rate("USD", datetime.date(2021, 5, 10))


def test_hmrc_response_invalid_rate_value_raises_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate value that is not a valid decimal raises ExternalApiError."""
    xml = (
        "<exchangeRateMonthList>"
        "<exchangeRate>"
        "<currencyCode>USD</currencyCode>"
        "<rateNew>not-a-rate</rateNew>"
        "</exchangeRate>"
        "</exchangeRateMonthList>"
    )
    converter = CurrencyConverter()
    monkeypatch.setattr(converter, "session", CannedSession(CannedResponse(xml)))

    with pytest.raises(ExternalApiError, match="contains invalid rate"):
        converter.currency_to_gbp_rate("USD", datetime.date(2021, 5, 10))
