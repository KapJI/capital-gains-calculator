"""Tests for attributing withheld tax to the dividend it was taken from."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import CurrencyCode
from cgt_calc.spin_off_handler import SpinOffHandler

from .calc_test_data import dividend_tax_transaction, dividend_transaction

if TYPE_CHECKING:
    import pytest

    from cgt_calc.model import BrokerTransaction

USD = CurrencyCode("USD")

# The tax year under test, two dates inside it a few days apart, and one in an
# earlier year, so that withholding can be placed on or off its dividend's date
# and inside or outside the reported year.
TAX_YEAR = 2024
DIVIDEND_DATE = datetime.date(2024, 6, 3)
LATER_DATE = datetime.date(2024, 6, 5)
EARLIER_YEAR_DATE = datetime.date(2022, 6, 3)


def _calculator(transactions: list[BrokerTransaction]) -> CapitalGainsCalculator:
    """Create a calculator with a flat 1:1 USD rate for the dates in use."""
    gbp_prices = {t.date: {USD: Decimal(1)} for t in transactions}
    currency_converter = CurrencyConverter(None, gbp_prices)
    return CapitalGainsCalculator(
        TAX_YEAR,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


def _unattributed_warnings(
    transactions: list[BrokerTransaction],
    caplog: pytest.LogCaptureFixture,
) -> list[str]:
    """Run the dividend pass and return the unattributed tax warnings."""
    calculator = _calculator(transactions)
    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        calculator.convert_to_hmrc_transactions(transactions)
        calculator.process_dividends()
    return [
        record.message
        for record in caplog.records
        if "is not attributed to any dividend" in record.message
    ]


def test_withholding_on_its_dividend_date_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax taken on the day of its dividend is attributed, so it is silent."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15),
    ]

    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_dated_apart_from_its_dividend_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax dated apart from its dividend is left out, so it is reported."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 15),
    ]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-15.00 USD for FOO on 2024-06-05" in warnings[0]


def test_withholding_without_any_dividend_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax for a symbol that paid no dividend at all is reported."""
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-15.00 USD for FOO on 2024-06-03" in warnings[0]


def test_unattributed_withholding_rounding_away_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax below report precision cannot change what the report shows."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 0.004),
    ]

    assert _unattributed_warnings(transactions, caplog) == []


def test_unattributed_withholding_of_half_a_penny_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Half a penny rounds up to one, which the report can show."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 0.005),
    ]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-0.01 USD for FOO" in warnings[0]


def test_unattributed_withholding_outside_tax_year_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only withholding of the computed year may warn, as for the treaty."""
    transactions = [
        dividend_transaction(EARLIER_YEAR_DATE, "FOO", 100),
        dividend_tax_transaction(
            EARLIER_YEAR_DATE + datetime.timedelta(days=2), "FOO", 15
        ),
    ]

    assert _unattributed_warnings(transactions, caplog) == []


def test_dividend_without_withholding_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dividend taxed nowhere leaves no zero entry to warn about."""
    transactions = [dividend_transaction(DIVIDEND_DATE, "FOO", 100)]
    calculator = _calculator(transactions)

    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        calculator.convert_to_hmrc_transactions(transactions)
        calculator.process_dividends()

    assert [
        record.message
        for record in caplog.records
        if "is not attributed to any dividend" in record.message
    ] == []
    assert (("FOO", DIVIDEND_DATE)) not in calculator.dividend_tax_list
