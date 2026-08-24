"""Tests for the dividend section of the first pass report."""

from decimal import Decimal

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import CurrencyCode
from cgt_calc.spin_off_handler import SpinOffHandler

GBP = CurrencyCode("GBP")


def _calculator() -> CapitalGainsCalculator:
    """Create a calculator with no transactions behind it."""
    currency_converter = CurrencyConverter(None, {})
    return CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


def _dividend_lines(
    capsys: pytest.CaptureFixture[str],
    dividends: dict[tuple[str, CurrencyCode], Decimal],
    dividends_tax: dict[tuple[str, CurrencyCode], Decimal],
) -> list[str]:
    """Run the report and return the printed dividend lines."""
    _calculator().first_pass_report({}, dividends, dividends_tax, {}, {})
    lines = capsys.readouterr().out.splitlines()
    header = next(i for i, line in enumerate(lines) if "Dividends" in line)
    section = lines[header + 1 :]
    return section[: section.index("")] if "" in section else section


def test_withholding_without_dividend_is_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tax withheld on a symbol that paid no dividend still shows up."""
    dividends = {("NDAQ", GBP): Decimal("1.10")}
    dividends_tax = {("NDAQ", GBP): Decimal("-0.17"), ("AAPL", GBP): Decimal("-0.30")}

    lines = _dividend_lines(capsys, dividends, dividends_tax)

    assert lines == [
        "  NDAQ: 1.10, excluding 0.17 taxed at source (GBP)",
        "  AAPL: 0.00, excluding 0.30 taxed at source (GBP)",
    ]


def test_sub_half_penny_withholding_is_not_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A withholding that rounds to 0.00 does not produce an empty note."""
    dividends = {("NDAQ", GBP): Decimal("1.10")}
    dividends_tax = {("NDAQ", GBP): Decimal("-0.004")}

    lines = _dividend_lines(capsys, dividends, dividends_tax)

    assert lines == ["  NDAQ: 1.10 (GBP)"]
