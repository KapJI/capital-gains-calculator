"""Unit and integration tests."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.util import round_decimal
from tests.utils import build_cmd

from .calc_test_data import calc_basic_data
from .calc_test_data_2 import calc_basic_data_2

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction, CalculationLog, CapitalGainsReport


def get_report(
    calculator: CapitalGainsCalculator, broker_transactions: list[BrokerTransaction]
) -> CapitalGainsReport:
    """Get calculation report."""
    calculator.convert_to_hmrc_transactions(broker_transactions)
    return calculator.calculate_capital_gain()


def test_main_prints_help_when_no_arguments() -> None:
    """Ensure CLI prints help text when invoked without arguments."""
    result = subprocess.run(
        [sys.executable, "-m", "cgt_calc.main"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "Calculate UK capital gains" in result.stdout


@pytest.mark.parametrize(
    (
        "tax_year",
        "broker_transactions",
        "expected",
        "expected_unrealized",
        "gbp_prices",
        "current_prices",
        "expected_uk_interest",
        "expected_foreign_interest",
        "expected_dividend",
        "expected_dividend_gain",
        "calculation_log",
        "calculation_log_yields",
    ),
    calc_basic_data + calc_basic_data_2,
)
def test_basic(
    tax_year: int,
    broker_transactions: list[BrokerTransaction],
    expected: float,
    expected_unrealized: float | None,
    gbp_prices: dict[datetime.date, dict[str, Decimal]] | None,
    current_prices: dict[str, Decimal | None] | None,
    expected_uk_interest: float,
    expected_foreign_interest: float,
    expected_dividend: float,
    expected_dividend_gain: float,
    calculation_log: CalculationLog | None,
    calculation_log_yields: CalculationLog | None,
) -> None:
    """Generate basic tests for test data."""
    if gbp_prices is None:
        gbp_prices = {t.date: {"USD": Decimal(1)} for t in broker_transactions}
    currency_converter = CurrencyConverter(None, gbp_prices)
    isin_converter = IsinConverter()
    historical_prices = {
        "FOO": {datetime.date(day=5, month=7, year=2023): Decimal(90)},
        "BAR": {datetime.date(day=5, month=7, year=2023): Decimal(12)},
    }
    price_fetcher = CurrentPriceFetcher(
        currency_converter, current_prices, historical_prices
    )
    spin_off_handler = SpinOffHandler()
    spin_off_handler.cache = {"BAR": "FOO"}
    initial_prices = InitialPrices()
    calculator = CapitalGainsCalculator(
        tax_year,
        currency_converter,
        isin_converter,
        price_fetcher,
        spin_off_handler,
        initial_prices,
        interest_fund_tickers=["FOO"],
        calc_unrealized_gains=expected_unrealized is not None,
    )
    report = get_report(calculator, broker_transactions)
    assert report.total_gain() == round_decimal(Decimal(expected), 2)
    print(str(report))
    if expected_unrealized is not None:
        assert report.total_unrealized_gains() == round_decimal(
            Decimal(expected_unrealized), 2
        )
    assert round_decimal(report.total_uk_interest, 2) == round_decimal(
        Decimal(expected_uk_interest), 2
    )
    assert round_decimal(report.total_foreign_interest, 2) == round_decimal(
        Decimal(expected_foreign_interest), 2
    )
    assert round_decimal(report.total_dividends_amount(), 2) == round_decimal(
        Decimal(expected_dividend), 2
    )
    assert round_decimal(report.total_dividend_taxable_gain(), 2) == round_decimal(
        Decimal(expected_dividend_gain), 2
    )
    if calculation_log is not None:
        result_log = report.calculation_log
        assert len(result_log) == len(calculation_log)
        for date_index, expected_entries_map in calculation_log.items():
            assert date_index in result_log
            result_entries_map = result_log[date_index]
            print(date_index)
            print(result_entries_map)
            assert len(result_entries_map) == len(expected_entries_map)
            for entries_type, expected_entries_list in expected_entries_map.items():
                assert entries_type in result_entries_map
                result_entries_list = result_entries_map[entries_type]
                assert len(result_entries_list) == len(expected_entries_list)
                for i, expected_entry in enumerate(expected_entries_list):
                    result_entry = result_entries_list[i]
                    assert result_entry.rule_type == expected_entry.rule_type
                    assert result_entry.quantity == expected_entry.quantity
                    assert result_entry.new_quantity == expected_entry.new_quantity
                    assert round_decimal(
                        result_entry.new_pool_cost, 4
                    ) == round_decimal(expected_entry.new_pool_cost, 4)
                    assert round_decimal(result_entry.gain, 4) == round_decimal(
                        expected_entry.gain, 4
                    )
                    assert round_decimal(result_entry.amount, 4) == round_decimal(
                        expected_entry.amount, 4
                    )
                    assert round_decimal(
                        result_entry.allowable_cost, 4
                    ) == round_decimal(expected_entry.allowable_cost, 4)
                    assert (
                        result_entry.bed_and_breakfast_date_index
                        == expected_entry.bed_and_breakfast_date_index
                    )
                    assert round_decimal(result_entry.fees, 4) == round_decimal(
                        expected_entry.fees, 4
                    )

    if calculation_log_yields is not None:
        result_log = report.calculation_log_yields
        assert len(result_log) == len(calculation_log_yields)
        for date_index, expected_entries_map in calculation_log_yields.items():
            assert date_index in result_log
            result_entries_map = result_log[date_index]
            print(date_index)
            print(result_entries_map)
            assert len(result_entries_map) == len(expected_entries_map)
            for entries_type, expected_entries_list in expected_entries_map.items():
                assert entries_type in result_entries_map
                result_entries_list = result_entries_map[entries_type]
                assert len(result_entries_list) == len(expected_entries_list)
                for i, expected_entry in enumerate(expected_entries_list):
                    result_entry = result_entries_list[i]
                    assert result_entry.rule_type == expected_entry.rule_type
                    assert round_decimal(result_entry.amount, 4) == round_decimal(
                        expected_entry.amount, 4
                    )


def test_run_with_example_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--schwab-file",
        "tests/schwab/data/schwab_transactions.csv",
        "--trading212-dir",
        "tests/trading212/data/2020/",
        "--mssb-dir",
        "tests/morgan_stanley/data/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    stderr_lines = result.stderr.strip().split("\n")
    expected_lines = 2
    assert len(stderr_lines) == expected_lines
    assert stderr_lines[0] == "WARNING: No Schwab Award file provided"
    assert stderr_lines[1].startswith("WARNING: Bed and breakfasting for VUAG"), (
        "Unexpected stderr message"
    )
    expected_file = (
        Path("tests") / "general" / "data" / "test_run_with_example_files_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
