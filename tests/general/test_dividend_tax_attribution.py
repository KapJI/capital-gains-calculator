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

# The tax year under test, a dividend inside it, and withholding placed at
# various distances from that dividend: two days away, exactly at the
# thirty-day limit, one day past it, and far outside.
TAX_YEAR = 2024
DIVIDEND_DATE = datetime.date(2024, 6, 3)
LATER_DATE = DIVIDEND_DATE + datetime.timedelta(days=2)
LIMIT_DATE = DIVIDEND_DATE + datetime.timedelta(days=30)
BEYOND_LIMIT_DATE = DIVIDEND_DATE + datetime.timedelta(days=31)

# A payment at the very end of 2024/25 whose withholding posts in 2025/26.
BORDER_DIVIDEND_DATE = datetime.date(2025, 4, 2)
BORDER_TAX_DATE = datetime.date(2025, 4, 8)


def _calculator(
    transactions: list[BrokerTransaction], tax_year: int = TAX_YEAR
) -> CapitalGainsCalculator:
    """Create a calculator with a flat 1:1 USD rate for the dates in use."""
    gbp_prices = {t.date: {USD: Decimal(1)} for t in transactions}
    currency_converter = CurrencyConverter(None, gbp_prices)
    return CapitalGainsCalculator(
        tax_year,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


def _reported(
    transactions: list[BrokerTransaction], tax_year: int = TAX_YEAR
) -> list[tuple[datetime.date, Decimal, Decimal, bool]]:
    """Return date, amount, tax and whether a treaty applied, per reported row."""
    calculator = _calculator(transactions, tax_year)
    calculator.convert_to_hmrc_transactions(transactions)
    calculator.process_dividends()
    rows = []
    for entries in calculator.calculation_log_yields.values():
        for key, log in entries.items():
            if not key.startswith("dividend$"):
                continue
            for entry in log:
                dividend = entry.dividend
                assert dividend is not None
                rows.append(
                    (
                        dividend.date,
                        dividend.amount,
                        dividend.tax_at_source,
                        dividend.tax_treaty is not None,
                    )
                )
    return sorted(rows)


def _unattributed_warnings(
    transactions: list[BrokerTransaction],
    caplog: pytest.LogCaptureFixture,
    tax_year: int = TAX_YEAR,
) -> list[str]:
    """Run the dividend pass and return the unattributed tax warnings."""
    # A test that also reports the run has already made the calculator warn
    # once, and those records must not be counted again here.
    caplog.clear()
    calculator = _calculator(transactions, tax_year)
    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        calculator.convert_to_hmrc_transactions(transactions)
        calculator.process_dividends()
    return [
        record.message
        for record in caplog.records
        if "is not attributed to any dividend" in record.message
    ]


def _summary_dividend_lines(
    transactions: list[BrokerTransaction],
    capsys: pytest.CaptureFixture[str],
    tax_year: int = TAX_YEAR,
) -> list[str]:
    """Return the dividend lines of the first pass summary."""
    # Discard whatever an earlier run in the same test printed.
    capsys.readouterr()
    calculator = _calculator(transactions, tax_year)
    calculator.convert_to_hmrc_transactions(transactions)
    lines = capsys.readouterr().out.splitlines()
    header = next((i for i, line in enumerate(lines) if "Dividends" in line), None)
    if header is None:
        return []
    section = lines[header + 1 :]
    return section[: section.index("")] if "" in section else section


def test_summary_and_report_agree_across_the_tax_year_end(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary puts withholding in the year the report puts it in.

    The summary used to count withholding in the year it was posted, so a
    payment and its tax either side of 5 April were reported in different
    years and contradicted the report.
    """
    transactions = [
        dividend_transaction(BORDER_DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BORDER_TAX_DATE, "FOO", 15),
    ]

    assert _summary_dividend_lines(transactions, capsys, tax_year=2024) == [
        "  FOO: 100.00, excluding 15.00 taxed at source (USD)"
    ]
    assert _reported(transactions, tax_year=2024) == [
        (BORDER_DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]

    assert _summary_dividend_lines(transactions, capsys, tax_year=2025) == []
    assert _reported(transactions, tax_year=2025) == []


def test_summary_counts_withholding_dated_apart_from_its_dividend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tax posted days after its dividend still reaches the summary."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 15),
    ]

    assert _summary_dividend_lines(transactions, capsys) == [
        "  FOO: 100.00, excluding 15.00 taxed at source (USD)"
    ]


def test_withholding_on_its_dividend_date_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax taken on the day of its dividend belongs to it, as it always did."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_dated_apart_from_its_dividend_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax posted a couple of days late still belongs to its dividend."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_correction_of_an_over_withholding_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Withholding at 30% later corrected to the treaty rate reports as 15%."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 30),
        dividend_tax_transaction(LATER_DATE, "FOO", -15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_each_dividend_keeps_its_own_withholding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax goes to the nearer payment when two could claim it."""
    second_date = datetime.date(2024, 9, 5)
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15),
        dividend_transaction(second_date, "FOO", 200),
        dividend_tax_transaction(second_date + datetime.timedelta(days=4), "FOO", 30),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True),
        (second_date, Decimal(200), Decimal(-30), True),
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_at_the_limit_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Thirty days away is still near enough to belong to the dividend."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LIMIT_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_beyond_the_limit_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A day past the limit is too far to attribute to the dividend."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BEYOND_LIMIT_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [(DIVIDEND_DATE, Decimal(100), Decimal(0), False)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-15.00 USD for FOO on 2024-07-04" in warnings[0]
    assert "within 30 days" in warnings[0]


def test_withholding_without_any_dividend_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax for a symbol that paid no dividend at all is reported."""
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-15.00 USD for FOO on 2024-06-03" in warnings[0]


def test_withholding_across_the_tax_year_end_stays_with_its_dividend() -> None:
    """A payment on 2 April keeps tax posted on 8 April, in the earlier year."""
    transactions = [
        dividend_transaction(BORDER_DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BORDER_TAX_DATE, "FOO", 15),
    ]

    assert _reported(transactions, tax_year=2024) == [
        (BORDER_DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    # The later year must not claim the same withholding a second time.
    assert _reported(transactions, tax_year=2025) == []


def test_unattributed_withholding_outside_tax_year_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only withholding of the computed year may warn, as for the treaty."""
    transactions = [dividend_tax_transaction(datetime.date(2022, 6, 3), "FOO", 15)]

    assert _unattributed_warnings(transactions, caplog) == []


def test_unattributed_withholding_below_a_penny_shows_its_own_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax too small to round to a penny is shown as it stands, not as zero.

    Whether such an amount is material depends on its value in GBP, which
    this warning deliberately does not work out, so it reports the loss and
    leaves that judgement to the reader.
    """
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 0.004)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "of -0.00 USD" not in warnings[0]
    assert "-0.004" in warnings[0]


def test_unattributed_withholding_of_half_a_penny_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Half a penny rounds up to one, which the report can show."""
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 0.005)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-0.01 USD for FOO" in warnings[0]


def test_dividend_without_withholding_leaves_no_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dividend taxed nowhere leaves no zero entry to warn about."""
    transactions = [dividend_transaction(DIVIDEND_DATE, "FOO", 100)]

    assert _reported(transactions) == [(DIVIDEND_DATE, Decimal(100), Decimal(0), False)]
    assert _unattributed_warnings(transactions, caplog) == []
