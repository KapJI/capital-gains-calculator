"""Tests for currency converter exchange rate loading."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, NoReturn

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.const import RuntimeMode
import cgt_calc.currency_converter
from cgt_calc.currency_converter import (
    CurrencyConverter,
    StrictTestCurrencyConverter,
    TestCurrencyConverter as RecordingCurrencyConverter,
)
from cgt_calc.exceptions import ExchangeRateMissingError, ExternalApiError, ParsingError

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


DATE = datetime.date(2024, 1, 1)


class FakeResponse:
    """Canned HMRC API response."""

    def __init__(self, *, ok: bool, status_code: int = 200, text: str = "") -> None:
        """Store response fields."""
        self.ok = ok
        self.status_code = status_code
        self.text = text


class FakeSession:
    """Session stub returning a canned response and recording the URL."""

    def __init__(self, response: FakeResponse) -> None:
        """Store the canned response."""
        self._response = response
        self.urls: list[str] = []

    def get(self, url: str, timeout: int) -> FakeResponse:
        """Record the URL and return the canned response."""
        self.urls.append(url)
        return self._response


def test_create_for_each_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the converter matching the runtime mode."""
    monkeypatch.setattr(cgt_calc.currency_converter, "CGT_MODE", RuntimeMode.PROD)
    assert type(CurrencyConverter.create()) is CurrencyConverter

    monkeypatch.setattr(
        cgt_calc.currency_converter, "CGT_MODE", RuntimeMode.TEST_STRICT
    )
    assert type(CurrencyConverter.create()) is StrictTestCurrencyConverter

    monkeypatch.setattr(cgt_calc.currency_converter, "CGT_MODE", RuntimeMode.TEST)
    assert type(CurrencyConverter.create()) is RecordingCurrencyConverter

    monkeypatch.setattr(cgt_calc.currency_converter, "CGT_MODE", "unknown")
    with pytest.raises(NotImplementedError):
        CurrencyConverter.create()


def test_read_exchange_rates_rejects_unexpected_columns(tmp_path: Path) -> None:
    """Reject rates files with a different schema."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate,extra\n2024-01-01,USD,1.25,x\n", encoding="utf8"
    )

    with pytest.raises(ParsingError, match="Unexpected columns"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_read_exchange_rates_rejects_missing_values(tmp_path: Path) -> None:
    """Reject rows with missing values."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n2024-01-01,USD,\n", encoding="utf8")

    with pytest.raises(ParsingError, match="Missing data"):
        CurrencyConverter(exchange_rates_file=rates_file)


def test_write_exchange_rates_file(tmp_path: Path) -> None:
    """Write the cache back as sorted CSV rows."""
    rates_file = tmp_path / "rates.csv"

    CurrencyConverter._write_exchange_rates_file(  # noqa: SLF001
        rates_file, {DATE: {"USD": Decimal("1.25"), "EUR": Decimal("1.10")}}
    )

    assert rates_file.read_text() == (
        "month,currency,rate\n2024-01-01,EUR,1.10\n2024-01-01,USD,1.25\n"
    )


def test_query_hmrc_api_old_endpoint_error_names_rates_file(
    tmp_path: Path,
) -> None:
    """Use the old endpoint before 2021 and mention the rates file on errors."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n", encoding="utf8")
    converter = CurrencyConverter(exchange_rates_file=rates_file)

    class FailingSession:
        def get(self, url: str, timeout: int) -> NoReturn:
            raise requests_exceptions.ConnectionError(f"offline: {url}")

    converter.session = FailingSession()  # type: ignore[assignment]

    with pytest.raises(ExternalApiError, match=r"rates\.csv") as excinfo:
        converter.currency_to_gbp_rate("USD", datetime.date(2019, 5, 1))

    assert "exrates-monthly-0519" in str(excinfo.value)


def test_query_hmrc_api_http_error_includes_snippet() -> None:
    """Include a truncated response body in HTTP errors."""
    converter = CurrencyConverter()
    response = FakeResponse(ok=False, status_code=500, text="x" * 300)
    converter.session = FakeSession(response)  # type: ignore[assignment]

    with pytest.raises(ExternalApiError, match="HTTP 500") as excinfo:
        converter.currency_to_gbp_rate("USD", DATE)

    assert "..." in str(excinfo.value)


def test_cnh_is_treated_as_cny() -> None:
    """Convert offshore yuan using the CNY rate."""
    converter = CurrencyConverter(initial_data={DATE: {"CNY": Decimal(9)}})

    assert converter.currency_to_gbp_rate("CNH", DATE) == Decimal(9)


def test_missing_currency_for_known_date() -> None:
    """Raise when the date is cached but the currency is missing."""
    converter = CurrencyConverter(initial_data={DATE: {"USD": Decimal(1)}})

    with pytest.raises(ExchangeRateMissingError):
        converter.currency_to_gbp_rate("EUR", DATE)


def test_test_converter_records_new_rates(tmp_path: Path) -> None:
    """Record rates fetched during tests in the rates file."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text("month,currency,rate\n", encoding="utf8")
    converter = RecordingCurrencyConverter(exchange_rates_file=rates_file)

    def fake_query(date: datetime.date) -> None:
        converter.cache[date] = {"USD": Decimal("1.25")}

    converter._query_hmrc_api = fake_query  # type: ignore[method-assign]  # noqa: SLF001

    assert converter.currency_to_gbp_rate("USD", DATE) == Decimal("1.25")
    assert "2024-01-01,USD,1.25" in rates_file.read_text()

    # Appending the same rate again is a no-op.
    RecordingCurrencyConverter._append_exchange_rates_file(  # noqa: SLF001
        rates_file, DATE, "USD", Decimal("1.25")
    )
    assert rates_file.read_text().count("USD") == 1


def test_strict_converter_refuses_to_fetch() -> None:
    """The CI converter never calls the HMRC API."""
    converter = StrictTestCurrencyConverter()

    with pytest.raises(RuntimeError, match="provided for tests"):
        converter.currency_to_gbp_rate("USD", DATE)
