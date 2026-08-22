"""Unit and integration tests."""

from __future__ import annotations

import datetime
from decimal import Decimal, localcontext
import logging
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.const import BALANCE_CHECK_CONTEXT_ROWS, RENAME_DESCRIPTION_PREFIX
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import (
    CalculationError,
    InvalidTransactionError,
    QuantityNotPositiveError,
    UnclassifiedGiftError,
)
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator, main
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalGainsReport,
    Isin,
    RuleType,
)
from cgt_calc.parsers.broker_registry import _transaction_sort_key
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.util import round_decimal
from tests.utils import build_cmd

from .calc_test_data import (
    buy_transaction,
    calc_basic_data,
    split_transaction,
    transaction,
    transfer_to_spouse_transaction,
    transfer_transaction,
)
from .calc_test_data_2 import calc_basic_data_2

if TYPE_CHECKING:
    import argparse

    from cgt_calc.model import CalculationLog


# USD to GBP exchange rate used in tests (creates repeating decimals)
USD_TO_GBP = Decimal(6) / Decimal(7)  # 0.857142857...


def gbp_from_usd(usd: str, qty: int) -> Decimal:
    """Convert USD amount to GBP for testing with repeating decimal exchange rate."""
    return Decimal(qty) * Decimal(usd) * USD_TO_GBP


def get_report(
    calculator: CapitalGainsCalculator, broker_transactions: list[BrokerTransaction]
) -> CapitalGainsReport:
    """Get calculation report."""
    calculator.convert_to_hmrc_transactions(broker_transactions)
    return calculator.calculate_capital_gain()


def create_calculator(
    tax_year: int = 2024, balance_check: bool = False
) -> CapitalGainsCalculator:
    """Create a calculator with standard test configuration."""
    currency_converter = CurrencyConverter(None, {})
    price_fetcher = CurrentPriceFetcher(currency_converter, {}, {})
    return CapitalGainsCalculator(
        tax_year,
        currency_converter,
        IsinConverter(),
        price_fetcher,
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=balance_check,
    )


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


def test_interest_tax_totals_are_positive() -> None:
    """Ensure interest tax totals are reported as positive amounts."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(None, {date: {"USD": Decimal(1)}})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    broker_transactions = [
        BrokerTransaction(
            date=date,
            action=ActionType.INTEREST_TAX,
            symbol=None,
            description="NRA Tax Adj",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("-20.31"),
            currency="USD",
            broker="Charles Schwab",
        )
    ]
    report = get_report(calculator, broker_transactions)
    assert report.total_interest_tax == Decimal("20.31")


def test_interest_tax_reversals_cancel_across_months() -> None:
    """A withholding reversed in a later month must net to zero tax."""
    march = datetime.date(2025, 3, 1)
    april = datetime.date(2025, 4, 1)
    currency_converter = CurrencyConverter(
        None, {march: {"USD": Decimal(1)}, april: {"USD": Decimal(1)}}
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    broker_transactions = [
        BrokerTransaction(
            date=march,
            action=ActionType.INTEREST_TAX,
            symbol=None,
            description="NRA Tax Adj",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("-5.00"),
            currency="USD",
            broker="Charles Schwab",
        ),
        BrokerTransaction(
            date=april,
            action=ActionType.INTEREST_TAX,
            symbol=None,
            description="NRA Tax Adj reversal",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("5.00"),
            currency="USD",
            broker="Charles Schwab",
        ),
    ]
    report = get_report(calculator, broker_transactions)
    assert report.total_interest_tax == Decimal(0)


ERI_ISIN = Isin("US5949181045")


def test_eri_duplicate_report_within_tolerance_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second ERI report within the 0.0001 tolerance is a harmless duplicate.

    ``add_eri`` compares consecutive reports for the same symbol/date with
    ``approx_equal(previous_price, price, Decimal("0.0001"))``. Prices this
    close should be treated as the same report arriving twice (e.g. from
    overlapping input files) and simply skipped with a warning.
    """
    date = datetime.date(2024, 6, 30)
    calculator = create_calculator()
    calculator.isin_converter.data[ERI_ISIN] = {"VWRL"}

    transactions: list[BrokerTransaction] = [
        ERITransaction(
            date=date, isin=ERI_ISIN, price=Decimal("1.00000"), currency="GBP"
        ),
        ERITransaction(
            date=date, isin=ERI_ISIN, price=Decimal("1.00005"), currency="GBP"
        ),
    ]

    calculator.convert_to_hmrc_transactions(transactions)

    assert "Skipping duplicated ERI transaction" in caplog.text
    assert calculator.eris[date]["VWRL"].price == Decimal("1.00000")


def test_eri_conflicting_report_raises_invalid_transaction_error() -> None:
    """A second ERI report that materially differs must raise, not be accepted.

    Regression test: ``approx_equal`` used to ignore its ``approx_quantity``
    argument and always compare against a hardcoded ``Decimal("0.01")``. Since
    ``add_eri`` calls it with a much tighter ``Decimal("0.0001")`` tolerance to
    tell apart a duplicate report from a genuinely conflicting one, that bug
    made any two ERI prices within 0.01 GBP of each other silently accepted as
    duplicates (first-seen price wins), even though they should have raised
    ``InvalidTransactionError``. Here the prices differ by 0.005, which is
    more than the intended 0.0001 tolerance but less than the old, buggy 0.01
    one.
    """
    date = datetime.date(2024, 6, 30)
    calculator = create_calculator()
    calculator.isin_converter.data[ERI_ISIN] = {"VWRL"}

    transactions: list[BrokerTransaction] = [
        ERITransaction(
            date=date, isin=ERI_ISIN, price=Decimal("1.0000"), currency="GBP"
        ),
        ERITransaction(
            date=date, isin=ERI_ISIN, price=Decimal("1.0050"), currency="GBP"
        ),
    ]

    with pytest.raises(InvalidTransactionError, match="conflicting ERI report"):
        calculator.convert_to_hmrc_transactions(transactions)


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
        assert len(result_log) == len(calculation_log), (
            f"Actual:\n{result_log}\n\nExpected:\n{calculation_log}\n\n"
        )
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


def test_bed_and_breakfast_zero_available_quantity_skip() -> None:
    """Later acquisitions are ignored if the disposal was already satisfied."""

    currency_converter = CurrencyConverter(None, {})
    price_fetcher = CurrentPriceFetcher(currency_converter, {}, {})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        price_fetcher,
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
    )

    symbol = "TEST"
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 1, 1),
            action=ActionType.TRANSFER,
            symbol=None,
            description="deposit",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal(500),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 1, 10),
            action=ActionType.BUY,
            symbol=symbol,
            description="initial buy",
            quantity=Decimal(10),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-100),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 3, 1),
            action=ActionType.SELL,
            symbol=symbol,
            description="disposal",
            quantity=Decimal(5),
            price=Decimal(12),
            fees=Decimal(0),
            amount=Decimal(60),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 3, 5),
            action=ActionType.BUY,
            symbol=symbol,
            description="bed and breakfast buy",
            quantity=Decimal(5),
            price=Decimal(11),
            fees=Decimal(0),
            amount=Decimal(-55),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 3, 10),
            action=ActionType.BUY,
            symbol=symbol,
            description="unrelated buy",
            quantity=Decimal(3),
            price=Decimal(9),
            fees=Decimal(0),
            amount=Decimal(-27),
            currency="GBP",
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    # The original disposal is fully matched against the 5-share buy, so no gain.
    assert report.total_gain() == Decimal(0)

    first_match = datetime.date(2024, 3, 5)
    assert calculator.bnb_list[first_match][symbol].quantity == Decimal(5)

    second_match = datetime.date(2024, 3, 10)
    assert symbol not in calculator.bnb_list.get(second_match, {})


def test_proportional_disposal_no_rounding_error() -> None:
    """Test that disposing all shares doesn't cause rounding errors.

    This test verifies the fix for the issue where sequential disposals
    using the divide-then-multiply pattern could accumulate rounding errors,
    causing assertions like "current amount -1E-23" to fail.

    The fix reorders operations from quantity * (amount / total) to
    (quantity * amount) / total, which ensures exact cancellation when
    disposing all shares: (total * amount) / total = amount.
    """
    calculator = create_calculator()
    symbol = "TEST"

    # Create scenario that would trigger rounding errors:
    # Buy 3 shares for £10, then dispose all 3 shares
    # Using 3 creates a repeating decimal (10/3 = 3.333...) which triggers the issue
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),  # Tax year 2024 (Apr 6, 2024 - Apr 5, 2025)
            action=ActionType.BUY,
            symbol=symbol,
            description="buy 3 shares",
            quantity=Decimal(3),
            price=Decimal("3.33"),
            fees=Decimal("0.01"),
            amount=Decimal("-10.00"),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 6, 1),  # Tax year 2024
            action=ActionType.SELL,
            symbol=symbol,
            description="sell all 3 shares",
            quantity=Decimal(3),
            price=Decimal("5.00"),
            fees=Decimal(0),
            amount=Decimal("15.00"),
            currency="GBP",
            broker="Test",
        ),
    ]

    # This should complete without AssertionError about rounding errors
    # Before the fix, this could fail with: AssertionError: current amount -1E-23
    report = get_report(calculator, transactions)

    # Verify the calculation completed successfully
    # The exact gain is: £15.00 proceeds - £10.00 cost = £5.00 gain
    assert report.total_gain() == Decimal("5.00")

    # Verify portfolio is now empty (all shares disposed)
    assert calculator.portfolio[symbol].quantity == Decimal(0)

    # Verify pool amount is exactly zero (no rounding errors)
    # This is the key assertion - without operation reordering,
    # the pool amount could be a tiny non-zero value like -1E-27
    assert calculator.portfolio[symbol].amount == Decimal(0), (
        "Pool amount should be exactly zero (no rounding error)"
    )


def test_high_precision_amount_no_rounding_error() -> None:
    """Test that high-precision amounts (29+ digits) don't cause rounding errors.

    This test uses USD->GBP currency conversion (6/7 exchange rate) which creates
    repeating decimals. When combined with realistic share quantities, the amounts
    accumulate 29+ significant digits of precision.

    Without the fix (28-digit precision), this fails with:
    AssertionError: current amount 2E-23
    """
    calculator = create_calculator()
    symbol = "ACME"

    transactions: list[BrokerTransaction] = [
        # Acquisition 1
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol=symbol,
            description="Vest",
            quantity=Decimal(10000),
            price=Decimal("50.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=-gbp_from_usd("50.00", 10000),
            currency="GBP",
            broker="Test",
        ),
        # Acquisition 2
        BrokerTransaction(
            date=datetime.date(2024, 6, 1),
            action=ActionType.BUY,
            symbol=symbol,
            description="Vest",
            quantity=Decimal(10000),
            price=Decimal("60.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=-gbp_from_usd("60.00", 10000),
            currency="GBP",
            broker="Test",
        ),
        # Sell all - triggers disposal with high-precision amounts
        BrokerTransaction(
            date=datetime.date(2024, 7, 1),
            action=ActionType.SELL,
            symbol=symbol,
            description="Sale",
            quantity=Decimal(20000),
            price=Decimal("70.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=gbp_from_usd("70.00", 20000),
            currency="GBP",
            broker="Test",
        ),
    ]

    # Without proper precision handling we get
    # AssertionError: current amount 2E-23
    get_report(calculator, transactions)

    assert calculator.portfolio[symbol].quantity == Decimal(0)
    assert calculator.portfolio[symbol].amount == Decimal(0), (
        f"Rounding error: {calculator.portfolio[symbol].amount}"
    )


def test_same_day_rule_all_shares_disposed_no_rounding_error() -> None:
    """Test that same day rule disposing ALL shares doesn't cause rounding errors."""
    calculator = create_calculator()
    symbol = "TEST"
    same_day = datetime.date(2024, 5, 1)

    # Use 3 shares to create 1/3 repeating decimal (strongest case)
    buy_quantity = Decimal(3)
    buy_amount_gbp = -gbp_from_usd("100.00", 3)  # Creates amount/3 repeating
    sell_quantity = Decimal(3)
    sell_amount_gbp = gbp_from_usd("120.00", 3)

    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=same_day,
            action=ActionType.BUY,
            symbol=symbol,
            description="buy 3 shares USD",
            quantity=buy_quantity,
            price=Decimal("100.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=buy_amount_gbp,
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=same_day,
            action=ActionType.SELL,
            symbol=symbol,
            description="sell all 3 shares same day",
            quantity=sell_quantity,
            price=Decimal("120.00") * USD_TO_GBP,
            fees=Decimal(0),
            amount=sell_amount_gbp,
            currency="GBP",
            broker="Test",
        ),
    ]

    # No AssertionError should get thrown here
    report = get_report(calculator, transactions)

    # Verify the gain calculation
    # Buy: 3 * £100 * (6/7) = £257.14, Sell: 3 * £120 * (6/7) = £308.57
    # Gain = £308.57 - £257.14 = £51.43
    assert report.total_gain() == Decimal("51.43")

    assert calculator.portfolio[symbol].quantity == Decimal(0)
    assert calculator.portfolio[symbol].amount == Decimal(0), (
        f"Pool amount should be exactly zero after disposing all shares on same day, "
        f"got {calculator.portfolio[symbol].amount}"
    )


def _rename_transaction(date: datetime.date, old: str, new: str) -> BrokerTransaction:
    """Build a RENAME BrokerTransaction that moves the pool from old to new."""
    return BrokerTransaction(
        date=date,
        action=ActionType.RENAME,
        symbol=new,
        description=f"{RENAME_DESCRIPTION_PREFIX}{old}",
        quantity=Decimal(0),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency="GBP",
        broker="Test",
    )


def test_rename_transfers_pool_to_new_ticker() -> None:
    """RENAME moves pool cost and quantity from old to new ticker; logs a RENAME entry."""
    calculator = create_calculator()
    rename_date = datetime.date(2024, 5, 10)
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD",
            quantity=Decimal(100),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency="GBP",
            broker="Test",
        ),
        _rename_transaction(rename_date, "OLD", "NEW"),
    ]

    report = get_report(calculator, transactions)

    assert "OLD" not in calculator.portfolio
    assert calculator.portfolio["NEW"].quantity == Decimal(100)
    assert calculator.portfolio["NEW"].amount == Decimal(1000)
    assert report.total_gain() == Decimal(0)

    entries = report.calculation_log[rename_date]["rename$OLD"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.rule_type is RuleType.RENAME
    assert entry.renamed_to == "NEW"
    assert entry.quantity == Decimal(100)
    assert entry.allowable_cost == Decimal(1000)
    assert entry.new_quantity == Decimal(100)
    assert entry.new_pool_cost == Decimal(1000)


def test_section_104_disposal_uses_renamed_pool_cost() -> None:
    """After a rename, S104 disposal under NEW uses the pool cost carried from OLD."""
    calculator = create_calculator()
    sell_date = datetime.date(2024, 6, 1)
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD tranche 1",
            quantity=Decimal(100),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 5, 2),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD tranche 2",
            quantity=Decimal(50),
            price=Decimal(20),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency="GBP",
            broker="Test",
        ),
        _rename_transaction(datetime.date(2024, 5, 15), "OLD", "NEW"),
        BrokerTransaction(
            date=sell_date,
            action=ActionType.SELL,
            symbol="NEW",
            description="partial sell NEW",
            quantity=Decimal(75),
            price=Decimal(20),
            fees=Decimal(0),
            amount=Decimal(1500),
            currency="GBP",
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    # Pool before sale: 150 units, £2,000 cost → £13.333.../unit.
    # Sell 75 units: cost £1,000, proceeds £1,500, gain £500.
    assert report.total_gain() == Decimal("500.00")
    sell_entries = report.calculation_log[sell_date]["sell$NEW"]
    assert len(sell_entries) == 1
    entry = sell_entries[0]
    assert entry.rule_type is RuleType.SECTION_104
    assert entry.quantity == Decimal(75)
    assert entry.allowable_cost == Decimal(1000)
    assert entry.gain == Decimal(500)
    # Remaining pool: 75 units at the same £13.333.../unit = £1,000.
    assert entry.new_quantity == Decimal(75)
    assert round_decimal(entry.new_pool_cost, 4) == Decimal(1000)
    assert calculator.portfolio["NEW"].quantity == Decimal(75)
    assert round_decimal(calculator.portfolio["NEW"].amount, 4) == Decimal(1000)


def test_bed_and_breakfast_matches_across_rename() -> None:
    """Sell of OLD is B&B-matched against a buy of NEW after a rename within 30 days."""
    calculator = create_calculator()
    transactions: list[BrokerTransaction] = [
        BrokerTransaction(
            date=datetime.date(2024, 5, 1),
            action=ActionType.BUY,
            symbol="OLD",
            description="buy OLD",
            quantity=Decimal(100),
            price=Decimal(10),
            fees=Decimal(0),
            amount=Decimal(-1000),
            currency="GBP",
            broker="Test",
        ),
        BrokerTransaction(
            date=datetime.date(2024, 5, 10),
            action=ActionType.SELL,
            symbol="OLD",
            description="sell OLD",
            quantity=Decimal(100),
            price=Decimal(8),
            fees=Decimal(0),
            amount=Decimal(800),
            currency="GBP",
            broker="Test",
        ),
        _rename_transaction(datetime.date(2024, 5, 15), "OLD", "NEW"),
        BrokerTransaction(
            date=datetime.date(2024, 5, 20),
            action=ActionType.BUY,
            symbol="NEW",
            description="rebuy under NEW",
            quantity=Decimal(100),
            price=Decimal(9),
            fees=Decimal(0),
            amount=Decimal(-900),
            currency="GBP",
            broker="Test",
        ),
    ]

    report = get_report(calculator, transactions)

    sell_date = datetime.date(2024, 5, 10)
    rebuy_date = datetime.date(2024, 5, 20)
    sell_entries = report.calculation_log[sell_date]["sell$OLD"]
    assert len(sell_entries) == 1
    sell_entry = sell_entries[0]
    assert sell_entry.rule_type is RuleType.BED_AND_BREAKFAST
    assert sell_entry.bed_and_breakfast_date_index == rebuy_date
    assert sell_entry.allowable_cost == Decimal(900)

    # 100 sold at £8 = £800 proceeds against £900 B&B cost → £100 loss.
    assert report.total_gain() == Decimal(-100)


def test_same_day_vest_ordered_before_sale() -> None:
    """A sale listed before the same-day vest is reordered so it can be processed.

    Equity-award exports (e.g. Schwab or Morgan Stanley) can list the sale of
    vested shares before the vest itself. A disposal is validated against the
    current holding as soon as it is read, so the raw order fails; the registry
    sort places the balance-neutral vest first so the sale is processed.
    """
    day = datetime.date(2024, 6, 3)
    vest = transaction(
        day, ActionType.STOCK_ACTIVITY, "SYM", quantity=10, price=10, currency="GBP"
    )
    sale = transaction(
        day, ActionType.SELL, "SYM", quantity=5, price=20, amount=100, currency="GBP"
    )

    # Raw order (sale before vest) fails the ownership check.
    with pytest.raises(InvalidTransactionError):
        get_report(create_calculator(), [sale, vest])

    # The registry sort orders the vest first, so the sale succeeds.
    report = get_report(
        create_calculator(), sorted([sale, vest], key=_transaction_sort_key)
    )
    assert report.total_gain() == Decimal(50)  # 5 * (20 - 10)


def test_same_day_sale_funding_purchase_survives_sort() -> None:
    """Ordering vests first must not disturb the 'sales before purchases' order.

    Only vests are moved, so a same-day sale that funds a purchase is still
    processed before the purchase and the running balance stays non-negative
    under the balance check.
    """
    day0 = datetime.date(2024, 6, 1)
    day1 = datetime.date(2024, 6, 2)
    transactions = [
        transaction(day0, ActionType.TRANSFER, amount=50, currency="GBP"),
        transaction(
            day0,
            ActionType.BUY,
            "SYM",
            quantity=5,
            price=10,
            amount=-50,
            currency="GBP",
        ),
        transaction(
            day1,
            ActionType.SELL,
            "SYM",
            quantity=5,
            price=20,
            amount=100,
            currency="GBP",
        ),
        transaction(
            day1,
            ActionType.BUY,
            "OTH",
            quantity=10,
            price=10,
            amount=-100,
            currency="GBP",
        ),
    ]

    report = get_report(
        create_calculator(balance_check=True),
        sorted(transactions, key=_transaction_sort_key),
    )
    assert report.total_gain() == Decimal(50)  # 5 * (20 - 10)


def test_disposal_debug_log_keeps_fractional_quantity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fractional share quantities must not be truncated in debug logs.

    Quantities were formatted with ``%d``, which truncates a ``Decimal`` to an
    integer (e.g. 2.5 -> 2). They must use ``%s`` so partial shares are logged
    in full.
    """
    buy_day = datetime.date(2024, 6, 1)
    sell_day = datetime.date(2024, 6, 10)
    transactions = [
        transaction(
            buy_day,
            ActionType.BUY,
            "SYM",
            quantity=5,
            price=10,
            amount=-50,
            currency="GBP",
        ),
        transaction(
            sell_day,
            ActionType.SELL,
            "SYM",
            quantity=2.5,
            price=20,
            amount=50,
            currency="GBP",
        ),
    ]

    with caplog.at_level(logging.DEBUG, logger="cgt_calc.main"):
        get_report(create_calculator(), transactions)

    disposal_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("DISPOSAL on")
    ]
    assert disposal_logs
    assert all("quantity 2.5" in message for message in disposal_logs)


def test_transfer_to_spouse_from_pool_is_no_gain_no_loss() -> None:
    """A transfer to spouse leaves the Section 104 pool at base cost with nil gain."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(buy_day, ActionType.BUY, "BAR", 5, 5, 0, -25, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 4),
            transfer_to_spouse_transaction(transfer_day, "BAR", 2),
        ],
    )

    # Not a taxable disposal.
    assert report.total_gain() == Decimal(0)
    assert report.disposal_count == 0
    assert report.disposal_proceeds == Decimal(0)

    # Pool reduced by 4 units at the £10 average cost.
    assert calculator.portfolio["FOO"].quantity == Decimal(6)
    assert calculator.portfolio["FOO"].amount == Decimal(60)

    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert all(e.rule_type is RuleType.TRANSFER_TO_SPOUSE for e in entries)
    assert sum((e.quantity for e in entries), Decimal(0)) == Decimal(4)
    assert sum((e.gain for e in entries), Decimal(0)) == Decimal(0)
    # £40 base cost passes to the recipient (4 units * £10 average).
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(40)

    # The second symbol transferred that day is handled independently.
    bar = report.calculation_log[transfer_day]["transfer-to-spouse$BAR"]
    assert sum((e.allowable_cost for e in bar), Decimal(0)) == Decimal(10)
    assert calculator.portfolio["BAR"].quantity == Decimal(3)


def test_transfer_to_spouse_matches_repurchase_within_30_days() -> None:
    """A purchase within 30 days after a transfer is matched first (s106A).

    The base cost passing to the spouse is then the cost of the re-purchased
    shares, not the Section 104 average - the case a plain pool reduction gets
    wrong.
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    rebuy_day = datetime.date(2024, 6, 15)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 5),
            transaction(rebuy_day, ActionType.BUY, "FOO", 5, 12, 0, -60, "GBP"),
        ],
    )

    assert report.total_gain() == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    # Matched to the £12 re-purchase, so base cost is £60 not the £50 average.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(60)
    # The original 10 @ £10 pool is left untouched by the transfer.
    assert calculator.portfolio["FOO"].quantity == Decimal(10)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_transfer_to_spouse_entire_holding_empties_pool() -> None:
    """Transferring the whole holding removes it from the portfolio with nil gain."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 10),
        ],
    )

    assert report.total_gain() == Decimal(0)
    assert calculator.portfolio["FOO"].quantity == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(100)


def test_transfer_to_spouse_matches_same_day_acquisition() -> None:
    """A purchase on the transfer day is matched first (same day rule).

    The base cost passing to the spouse is then the cost of the shares bought
    that day, not the Section 104 average.
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(transfer_day, ActionType.BUY, "FOO", 3, 20, 0, -60, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 3),
        ],
    )

    assert report.total_gain() == Decimal(0)
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    # Matched to the £20 same-day purchase, so base cost is £60 not the £10 average.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(60)
    # The original 10 @ £10 pool is left untouched by the transfer.
    assert calculator.portfolio["FOO"].quantity == Decimal(10)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


@pytest.mark.parametrize(
    "acquisition_day",
    [
        pytest.param(datetime.date(2024, 6, 10), id="same-day"),
        pytest.param(datetime.date(2024, 7, 9), id="within-30-days"),
    ],
)
def test_transfer_to_spouse_same_day_as_sale_is_rejected(
    acquisition_day: datetime.date,
) -> None:
    """A sale and a transfer competing for the same acquisitions is unsupported."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    with pytest.raises(CalculationError, match="does not say how to split") as err:
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"),
                transfer_to_spouse_transaction(event_day, "FOO", 2),
                transaction(
                    acquisition_day, ActionType.BUY, "FOO", 4, 30, 0, -120, "GBP"
                ),
            ],
        )

    # The message names the clash concretely so it can be acted on.
    message = str(err.value)
    assert "sold 3 units of FOO and transferred 2" in message
    assert str(acquisition_day) in message


def test_same_day_sale_and_transfer_after_a_split_is_allowed() -> None:
    """A split is not an acquisition a sale and a transfer can contend over.

    It hands over shares for free, so the B&B rule skips it and neither
    disposal can be identified against it. Both fall through to the pool.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    split_day = datetime.date(2024, 6, 15)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
            split_transaction(split_day, "FOO", 5),
        ],
    )

    entries = report.calculation_log[event_day]["transfer-to-spouse$FOO"]
    # Straight out of the pool at the £10 average.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)


def test_transfer_to_spouse_same_day_as_sale_from_pool_is_allowed() -> None:
    """A sale and a transfer both drawing on the pool alone are unambiguous.

    Neither can be identified against an acquisition, so both take the same
    Section 104 average cost and the order between them cannot matter.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
        ],
    )

    # Only the sale is taxable: £60 proceeds against a £30 allowable cost.
    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(60)
    assert report.total_gain() == Decimal(30)

    entries = report.calculation_log[event_day]["transfer-to-spouse$FOO"]
    # The transfer takes the same £10 average, so £20 passes to the recipient.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)
    assert calculator.portfolio["FOO"].quantity == Decimal(5)
    assert calculator.portfolio["FOO"].amount == Decimal(50)


def test_transfer_to_spouse_more_than_owned_is_rejected() -> None:
    """Transferring more units than held is refused."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    with pytest.raises(InvalidTransactionError, match="more than the available"):
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transfer_to_spouse_transaction(transfer_day, "FOO", 11),
            ],
        )


def test_transfer_to_spouse_of_not_owned_symbol_is_rejected() -> None:
    """Transferring a symbol that was never acquired is refused."""
    calculator = create_calculator()
    with pytest.raises(InvalidTransactionError, match="not owned symbol"):
        get_report(
            calculator,
            [transfer_to_spouse_transaction(datetime.date(2024, 6, 10), "FOO", 1)],
        )


def test_transfer_to_spouse_warns_about_the_ignored_price(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gift has no consideration, so any price on the row is ignored."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        report = get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(
                    transfer_day,
                    ActionType.TRANSFER_TO_SPOUSE,
                    "FOO",
                    4,
                    25,
                    0,
                    0,
                    "GBP",
                ),
            ],
        )

    assert "Ignoring the price" in caplog.text
    # Still the pool average, not the £25 that was in the price column.
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(40)


def test_transfer_to_spouse_fee_is_added_to_the_base_cost() -> None:
    """A fee is an incidental cost of the disposal, so the recipient inherits it.

    s58 fixes the consideration at the amount giving neither gain nor loss, and
    that amount has to cover the fee, so the fee ends up in the base cost the
    recipient takes on (TCGA 1992 s38(2), CG15250).
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(
                transfer_day,
                ActionType.TRANSFER_TO_SPOUSE,
                "FOO",
                4,
                None,
                5,
                -5,
                "GBP",
            ),
        ],
    )

    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    # £40 of pool cost plus the £5 fee.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(45)
    # Still no gain to the transferor.
    assert sum((e.gain for e in entries), Decimal(0)) == Decimal(0)
    assert report.total_gain() == Decimal(0)
    # The fee does not come out of the pool the transferor keeps.
    assert calculator.portfolio["FOO"].amount == Decimal(60)


def test_transfer_to_spouse_shown_in_report() -> None:
    """The text report lists each transfer with the base cost passed on."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 1000, 10, 0, -10000, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 400),
        ],
    )

    output = str(report)
    assert "Transferred to spouse" in output
    # Quantities are shown as entered, like the PDF, so fractions survive.
    assert f"{transfer_day}: FOO 400 units, base cost £4,000.00" in output


def test_unclassified_gift_is_rejected_with_instructions() -> None:
    """A broker gift with no classification refuses and says how to fix it."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    with pytest.raises(UnclassifiedGiftError) as err:
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
            ],
        )

    message = str(err.value)
    # The exact RAW line to paste, so the fix is mechanical.
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,4,0.00,0.00,GBP" in message
    # Both outcomes are named, so a non-spouse gift is not quietly treated as one.
    assert "spouse or civil partner" in message
    assert "disposal at market value" in message


def test_gift_matched_by_transfer_to_spouse_is_accounted_for() -> None:
    """A matching transfer row classifies the gift, which is then not counted twice."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
            transfer_to_spouse_transaction(gift_day, "FOO", 4),
        ],
    )

    assert report.total_gain() == Decimal(0)
    # Four units left once, not twice: the broker row is redundant, not extra.
    assert calculator.portfolio["FOO"].quantity == Decimal(6)
    assert calculator.portfolio["FOO"].amount == Decimal(60)
    entries = report.calculation_log[gift_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(40)


def test_gift_with_mismatched_transfer_is_rejected() -> None:
    """A transfer row that does not match the gift does not classify it."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    with pytest.raises(UnclassifiedGiftError):
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
                # Same day and symbol, different quantity.
                transfer_to_spouse_transaction(gift_day, "FOO", 3),
            ],
        )


def test_transfer_to_spouse_without_quantity_is_rejected() -> None:
    """A transfer row has to say how many units moved."""
    buy_day = datetime.date(2024, 6, 1)
    calculator = create_calculator()
    with pytest.raises(QuantityNotPositiveError):
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transfer_to_spouse_transaction(datetime.date(2024, 6, 10), "FOO", 0),
            ],
        )


def test_gift_without_quantity_is_rejected() -> None:
    """A gift row with no quantity is refused before asking who received it."""
    calculator = create_calculator()
    with pytest.raises(QuantityNotPositiveError):
        get_report(
            calculator,
            [
                transaction(
                    datetime.date(2024, 6, 10),
                    ActionType.GIFT,
                    "FOO",
                    0,
                    currency="GBP",
                )
            ],
        )


def test_transfer_to_spouse_reserves_its_share_of_a_same_day_acquisition() -> None:
    """An earlier sale cannot bed-and-breakfast shares a later transfer will claim.

    The transfer is same-day with the repurchase, and the same-day rule beats
    bed and breakfast, so the sale has to leave those shares alone or they get
    counted twice.
    """
    buy_day = datetime.date(2024, 6, 1)
    sell_day = datetime.date(2024, 6, 10)
    rebuy_day = datetime.date(2024, 6, 15)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(sell_day, ActionType.SELL, "FOO", 5, 20, 0, 100, "GBP"),
            transaction(rebuy_day, ActionType.BUY, "FOO", 5, 30, 0, -150, "GBP"),
            transfer_to_spouse_transaction(rebuy_day, "FOO", 5),
        ],
    )

    # The sale falls through to the pool at £10 rather than matching the £30
    # repurchase, so the gain is £100 - £50 rather than £100 - £150.
    assert report.total_gain() == Decimal(50)
    sale = report.calculation_log[sell_day]["sell$FOO"]
    assert all(e.rule_type is RuleType.SECTION_104 for e in sale)
    # The transfer takes the repurchase under the same-day rule.
    entries = report.calculation_log[rebuy_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(150)
    assert calculator.portfolio["FOO"].quantity == Decimal(5)
    assert calculator.portfolio["FOO"].amount == Decimal(50)


def test_same_day_sale_and_transfer_message_caps_the_dates_it_lists() -> None:
    """Too many contended acquisitions are summarised rather than all listed."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    rebuys = [
        transaction(
            event_day + datetime.timedelta(days=offset),
            ActionType.BUY,
            "FOO",
            1,
            30,
            0,
            -30,
            "GBP",
        )
        for offset in (1, 2, 3, 4)
    ]
    with pytest.raises(CalculationError) as err:
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"),
                transfer_to_spouse_transaction(event_day, "FOO", 2),
                *rebuys,
            ],
        )

    message = str(err.value)
    assert "2024-06-11, 2024-06-12, 2024-06-13 and 1 more" in message


def test_transfer_to_spouse_logs_a_pending_spin_off() -> None:
    """A spin-off still pending when the transfer happens is recorded.

    The transfer is identified like a disposal, so it is the event that
    applies the outstanding cost-proportion adjustment and has to report it.
    """
    buy_day = datetime.date(2023, 6, 1)
    spin_off_day = datetime.date(2023, 7, 5)
    transfer_day = datetime.date(2024, 6, 10)
    currency_converter = CurrencyConverter(None, {})
    spin_off_handler = SpinOffHandler()
    spin_off_handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(
            currency_converter,
            {},
            {
                "FOO": {spin_off_day: Decimal(90)},
                "BAR": {spin_off_day: Decimal(10)},
            },
        ),
        spin_off_handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 100, 10, 0, -1000, "GBP"),
            transaction(
                spin_off_day, ActionType.SPIN_OFF, "BAR", 100, 10, 0, currency="GBP"
            ),
            transfer_to_spouse_transaction(transfer_day, "FOO", 50),
        ],
    )

    assert report.total_gain() == Decimal(0)
    # 90/(90+10) of the £1,000 stays with FOO, so 50 of 100 units carry £450.
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(450)
    # The transfer applied the adjustment, so it reports the spin-off too.
    spin_off_entries = report.calculation_log[spin_off_day]["spin-off$FOO"]
    assert len(spin_off_entries) == 1
    assert spin_off_entries[0].rule_type is RuleType.SPIN_OFF


def test_transfer_to_spouse_before_the_tax_year_still_reduces_the_pool() -> None:
    """A historical transfer shapes the pool without appearing in the report.

    The whole history is walked to build the pool, but only events inside the
    reported period belong in the calculations.
    """
    buy_day = datetime.date(2022, 5, 1)
    transfer_day = datetime.date(2022, 6, 10)
    sell_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 4),
            transaction(sell_day, ActionType.SELL, "FOO", 6, 20, 0, 120, "GBP"),
        ],
    )

    assert transfer_day not in report.calculation_log
    # The pool lost 4 units at £10, so the later sale has a £60 allowable cost.
    assert report.disposal_count == 1
    assert report.total_gain() == Decimal(60)


def test_disposal_is_not_matched_against_a_same_day_split() -> None:
    """A split is not an acquisition, so it cannot be matched on the same day.

    TCGA 1992 s127 treats the new shares as acquired when the original ones
    were (CG51805). Matching against them would give the disposal their nil
    cost and leave the whole pool cost behind on fewer shares.
    """
    buy_day = datetime.date(2024, 6, 1)
    split_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            split_transaction(split_day, "FOO", 10),
            transaction(split_day, ActionType.SELL, "FOO", 4, 12, 0, 48, "GBP"),
        ],
    )

    entries = report.calculation_log[split_day]["sell$FOO"]
    assert all(e.rule_type is RuleType.SECTION_104 for e in entries)
    # 20 units hold £100, so 4 of them cost £20, not £0.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)
    assert report.total_gain() == Decimal(28)
    assert calculator.portfolio["FOO"].amount == Decimal(80)


def test_transfer_to_spouse_is_not_matched_against_a_same_day_split() -> None:
    """The same, for a transfer: the recipient must not inherit a nil base cost."""
    buy_day = datetime.date(2024, 6, 1)
    split_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            split_transaction(split_day, "FOO", 10),
            transfer_to_spouse_transaction(split_day, "FOO", 4),
        ],
    )

    entries = report.calculation_log[split_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)
    assert calculator.portfolio["FOO"].quantity == Decimal(16)
    assert calculator.portfolio["FOO"].amount == Decimal(80)


def test_each_gift_needs_its_own_classification() -> None:
    """Two gifts of the same shares on one day are not settled by one row."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    with pytest.raises(UnclassifiedGiftError):
        get_report(
            calculator,
            [
                transaction(buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, "GBP"),
                transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
                transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
                transfer_to_spouse_transaction(gift_day, "FOO", 4),
            ],
        )


def test_two_gifts_are_settled_by_two_classifications() -> None:
    """Matching one row per gift accounts for both."""
    buy_day = datetime.date(2024, 6, 1)
    gift_day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, "GBP"),
            transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
            transaction(gift_day, ActionType.GIFT, "FOO", 4, currency="GBP"),
            transfer_to_spouse_transaction(gift_day, "FOO", 4),
            transfer_to_spouse_transaction(gift_day, "FOO", 4),
        ],
    )

    assert report.total_gain() == Decimal(0)
    # Both transfers left the pool: 20 - 8.
    assert calculator.portfolio["FOO"].quantity == Decimal(12)
    assert calculator.portfolio["FOO"].amount == Decimal(120)


def test_transfer_to_spouse_sorts_after_a_same_day_acquisition() -> None:
    """A hand-written transfer must not be read before the buy it depends on.

    Transfers come from a RAW file while the shares come from a broker export,
    and parsers merge in registry order, so without this the transfer is
    validated first and fails as "not owned".
    """
    day = datetime.date(2024, 6, 10)
    buy = transaction(day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP")
    transfer = transfer_to_spouse_transaction(day, "FOO", 4)

    # Worst case: the transfer is read first, as a RAW file would give it.
    report = get_report(
        create_calculator(),
        sorted([transfer, buy], key=_transaction_sort_key),
    )

    entries = report.calculation_log[day]["transfer-to-spouse$FOO"]
    assert sum((e.quantity for e in entries), Decimal(0)) == Decimal(4)


def test_management_fee_is_not_a_contended_acquisition() -> None:
    """A fee buys no shares, so it cannot be something two disposals fight over.

    Fees are recorded in the acquisition list with a cost and no quantity, so
    a naive "has a cost" test mistakes one for a purchase.
    """
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    fee_day = datetime.date(2024, 6, 20)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
            transaction(fee_day, ActionType.FEE, "FOO", None, None, 0, -1, "GBP"),
        ],
    )

    assert report.total_gain() == Decimal(30)
    entries = report.calculation_log[event_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(20)


def test_fractional_transfer_keeps_its_units_in_the_report() -> None:
    """Rounding to two places would report a real transfer as zero units."""
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    report = get_report(
        create_calculator(),
        [
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transfer_to_spouse_transaction(transfer_day, "FOO", 0.001),
        ],
    )

    assert "FOO 0.001 units" in str(report)


def test_split_shares_do_not_dilute_a_same_day_purchase() -> None:
    """A split on the day of a purchase must not spread its cost over free shares.

    Both land in the acquisition list for the day, so treating the total as one
    acquisition prices the purchase across shares that cost nothing.
    """
    day = datetime.date(2024, 6, 10)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1), ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"
            ),
            split_transaction(day, "FOO", 10),
            transaction(day, ActionType.BUY, "FOO", 5, 20, 0, -100, "GBP"),
            transfer_to_spouse_transaction(day, "FOO", 5),
        ],
    )

    entries = report.calculation_log[day]["transfer-to-spouse$FOO"]
    # The five shares bought that day cost £100, and that is what is passed on.
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(100)
    # 10 original plus 10 free shares, still holding the original £100.
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


def test_transfer_reservation_survives_a_split_before_the_repurchase() -> None:
    """Reservations are counted in the acquisition's units, not the disposal's.

    A split between an earlier sale and the repurchase means the two are
    counted differently; subtracting one from the other unconverted drove the
    available quantity negative.
    """
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1), ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"
            ),
            transaction(
                datetime.date(2024, 6, 5), ActionType.SELL, "FOO", 4, 15, 0, 60, "GBP"
            ),
            split_transaction(datetime.date(2024, 6, 8), "FOO", 6),
            transaction(
                datetime.date(2024, 6, 12), ActionType.BUY, "FOO", 10, 5, 0, -50, "GBP"
            ),
            transfer_to_spouse_transaction(datetime.date(2024, 6, 12), "FOO", 6),
        ],
    )

    assert report.disposal_count == 1
    assert report.total_gain() == Decimal(20)


def test_fully_matched_acquisition_is_not_contended() -> None:
    """An acquisition its own day's sale consumes cannot reach an earlier day."""
    buy_day = datetime.date(2024, 6, 1)
    event_day = datetime.date(2024, 6, 10)
    later = datetime.date(2024, 6, 15)
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.BUY, "FOO", 20, 10, 0, -200, "GBP"),
            transaction(event_day, ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"),
            transfer_to_spouse_transaction(event_day, "FOO", 2),
            transaction(later, ActionType.BUY, "FOO", 4, 30, 0, -120, "GBP"),
            transaction(later, ActionType.SELL, "FOO", 4, 31, 0, 124, "GBP"),
        ],
    )

    # £30 on the 10th (3 units at £10 cost) plus £4 on the 15th.
    assert report.total_gain() == Decimal(34)


def test_transfer_fee_does_not_move_the_cash_balance() -> None:
    """A hand-written transfer row has no funded ledger to charge the fee to.

    RAW rows come in under their own broker, so charging the fee there drives
    that balance straight below zero and trips the balance check.
    """
    buy_day = datetime.date(2024, 6, 1)
    transfer_day = datetime.date(2024, 6, 10)
    transfer = BrokerTransaction(
        date=transfer_day,
        action=ActionType.TRANSFER_TO_SPOUSE,
        symbol="FOO",
        description="",
        quantity=Decimal(4),
        price=Decimal(0),
        fees=Decimal(5),
        amount=Decimal(-5),
        currency="GBP",
        broker="Unknown",
    )
    calculator = create_calculator(balance_check=True)
    report = get_report(
        calculator,
        [
            transaction(buy_day, ActionType.TRANSFER, None, None, None, 0, 200, "GBP"),
            transaction(buy_day, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transfer,
        ],
    )

    # The fee still reaches the recipient's base cost: £40 of pool plus £5.
    entries = report.calculation_log[transfer_day]["transfer-to-spouse$FOO"]
    assert sum((e.allowable_cost for e in entries), Decimal(0)) == Decimal(45)


def test_acquisition_an_earlier_disposal_took_is_not_contended() -> None:
    """A settled bed and breakfast match leaves nothing to argue over.

    The 5 June sale takes the whole 15 June purchase, so neither the sale nor
    the transfer on 10 June can reach it and both fall to the pool.
    """
    calculator = create_calculator()
    report = get_report(
        calculator,
        [
            transaction(
                datetime.date(2024, 6, 1), ActionType.BUY, "FOO", 20, 10, 0, -200, "GBP"
            ),
            transaction(
                datetime.date(2024, 6, 5), ActionType.SELL, "FOO", 4, 15, 0, 60, "GBP"
            ),
            transaction(
                datetime.date(2024, 6, 10), ActionType.SELL, "FOO", 3, 20, 0, 60, "GBP"
            ),
            transfer_to_spouse_transaction(datetime.date(2024, 6, 10), "FOO", 2),
            transaction(
                datetime.date(2024, 6, 15), ActionType.BUY, "FOO", 4, 30, 0, -120, "GBP"
            ),
        ],
    )

    # £60 loss bed-and-breakfasted on 5 June, £30 gain from the pool on the 10th.
    assert report.total_gain() == Decimal(-30)
    entries = report.calculation_log[datetime.date(2024, 6, 10)][
        "transfer-to-spouse$FOO"
    ]
    assert all(e.rule_type is RuleType.TRANSFER_TO_SPOUSE for e in entries)


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
        "--output",
        "out/test-general/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    # The progress narrative is a contract: it must reach stderr, in order.
    # The last two lines depend on whether pdflatex runs, so match by prefix.
    stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert stderr_lines[:-2] == [
        "Parsing tests/schwab/data/schwab_transactions.csv...",
        "Loaded 13 transactions from Charles Schwab",
        "Parsing tests/morgan_stanley/data/Releases Report.csv...",
        "Parsing tests/morgan_stanley/data/Withdrawals Report.csv...",
        "Loaded 6 transactions from Morgan Stanley",
        "Parsing tests/trading212/data/2020/from_2020-09-11_to_2021-04-02.csv...",
        "Loaded 8 transactions from Trading 212",
        "Found 27 broker transactions",
        "First pass complete",
        "Bed & breakfast match: VUAG disposed 2020-06-03, re-acquired 2020-07-02",
        "Second pass complete",
    ]
    assert stderr_lines[-2].startswith("Writing ")
    assert stderr_lines[-1].startswith("Done!")
    expected_file = (
        Path("tests") / "general" / "data" / "test_run_with_example_files_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_main_returns_failure_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception must produce a failure exit code."""

    def explode(args: argparse.Namespace) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("cgt_calc.main.calculate_cgt", explode)
    monkeypatch.setattr(sys, "argv", ["cgt-calc", "--year", "2021"])

    # main() enables the FloatOperation trap on the active decimal context;
    # keep that from leaking into other tests in the same worker.
    with localcontext():
        assert main() == 1


def test_negative_balance_error_trims_long_history() -> None:
    """A long transaction dump is trimmed to the most recent entries."""
    start = datetime.date(2024, 5, 1)
    transactions = [transfer_transaction(start, 13.0)] + [
        transfer_transaction(start + datetime.timedelta(days=day), -1.0)
        for day in range(1, 15)
    ]
    calculator = create_calculator(balance_check=True)

    with pytest.raises(CalculationError) as excinfo:
        calculator.convert_to_hmrc_transactions(transactions)

    message = str(excinfo.value)
    assert "... 5 earlier transaction(s) omitted ..." in message
    assert message.count("Balance after transaction=") == BALANCE_CHECK_CONTEXT_ROWS
    assert "use --no-balance-check" in message


def test_negative_balance_error_shows_short_history_in_full() -> None:
    """A dump that fits within the limit is shown without omissions."""
    transactions = [transfer_transaction(datetime.date(2024, 5, 1), -1.0)]
    calculator = create_calculator(balance_check=True)

    with pytest.raises(CalculationError) as excinfo:
        calculator.convert_to_hmrc_transactions(transactions)

    message = str(excinfo.value)
    assert "Reached a negative balance(-1.000000)" in message
    assert "omitted" not in message
    assert message.count("Balance after transaction=") == 1
    assert "use --no-balance-check" in message


def test_negative_balance_error_shows_only_relevant_transactions() -> None:
    """Other currencies and cash-neutral transactions are left out of the dump."""
    day = datetime.date(2024, 5, 1)
    transactions = [
        transfer_transaction(day, 100.0),
        buy_transaction(
            day + datetime.timedelta(days=1),
            "FOO",
            quantity=1,
            price=10.0,
            fees=0.0,
            amount=-10.0,
        ),
        split_transaction(day + datetime.timedelta(days=2), "FOO", quantity=1),
        transaction(
            day + datetime.timedelta(days=3),
            ActionType.TRANSFER,
            amount=5.0,
            currency="EUR",
        ),
        transfer_transaction(day + datetime.timedelta(days=4), -91.0),
    ]
    calculator = create_calculator(balance_check=True)

    with pytest.raises(CalculationError) as excinfo:
        calculator.convert_to_hmrc_transactions(transactions)

    message = str(excinfo.value)
    assert message.count("Balance after transaction=") == 3
    assert "currency='EUR'" not in message
    assert "Split of FOO" not in message


def test_custom_period_narrows_reporting_window() -> None:
    """Only dates inside the custom period count towards the report."""
    currency_converter = CurrencyConverter(None, {})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
        period_start=datetime.date(2024, 4, 6),
        period_end=datetime.date(2024, 10, 29),
    )

    assert calculator.date_in_tax_year(datetime.date(2024, 10, 29))
    assert not calculator.date_in_tax_year(datetime.date(2024, 10, 30))
    assert calculator.tax_year_end_date == datetime.date(2024, 10, 29)


def test_report_labels_custom_period() -> None:
    """Report headings and title show the custom period when set."""
    report = CapitalGainsReport(
        2024,
        [],
        0,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        None,
        None,
        {},
        {},
        Decimal(0),
        Decimal(0),
        Decimal(0),
        show_unrealized_gains=False,
        period_start=datetime.date(2024, 4, 6),
        period_end=datetime.date(2024, 10, 29),
    )

    assert report.title_period == "2024-04-06 to 2024-10-29"
    assert "period 2024-04-06 to 2024-10-29" in str(report)


def test_report_labels_full_tax_year() -> None:
    """Report headings keep the tax year wording without a custom period."""
    report = CapitalGainsReport(
        2024,
        [],
        0,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        None,
        None,
        {},
        {},
        Decimal(0),
        Decimal(0),
        Decimal(0),
        show_unrealized_gains=False,
    )

    assert report.title_period == "2024-25"
    assert "Tax summary for 2024/2025" in str(report)


def test_foreign_fees_folded_into_gbp_transaction() -> None:
    """Convert foreign fees to the transaction currency and re-derive price."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(None, {date: {"USD": Decimal("1.25")}})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    buy = BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal(10),
        fees=Decimal(0),
        amount=Decimal(-100),
        currency="GBP",
        broker="Trading212",
        foreign_fees={"USD": Decimal("1.25")},
    )

    calculator.convert_to_hmrc_transactions([buy])

    assert buy.foreign_fees == {}
    assert buy.fees == Decimal(1)
    assert buy.price == Decimal("9.9")


def test_foreign_fees_folded_into_non_gbp_transaction() -> None:
    """Convert GBP fees into a non-GBP transaction currency."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(None, {date: {"USD": Decimal("1.25")}})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    buy = BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal("12.5"),
        fees=Decimal(0),
        amount=Decimal(-125),
        currency="USD",
        broker="Trading212",
        foreign_fees={"GBP": Decimal(1)},
    )

    calculator.convert_to_hmrc_transactions([buy])

    assert buy.foreign_fees == {}
    assert buy.fees == Decimal("1.25")
    assert buy.price == Decimal("12.375")


def test_multiple_foreign_fee_currencies_on_sell() -> None:
    """Convert each foreign fee currency and re-derive the sell price."""
    date = datetime.date(2024, 5, 1)
    currency_converter = CurrencyConverter(
        None, {date: {"USD": Decimal("1.25"), "EUR": Decimal("1.10")}}
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    buy = BrokerTransaction(
        date=date,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal(9),
        fees=Decimal(0),
        amount=Decimal(-90),
        currency="GBP",
        broker="Trading212",
    )
    sell = BrokerTransaction(
        date=date,
        action=ActionType.SELL,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(10),
        price=Decimal("9.93"),
        fees=Decimal(0),
        amount=Decimal("99.30"),
        currency="GBP",
        broker="Trading212",
        foreign_fees={"USD": Decimal("0.25"), "EUR": Decimal("0.55")},
    )

    calculator.convert_to_hmrc_transactions([buy, sell])

    assert sell.foreign_fees == {}
    assert sell.fees == Decimal("0.70")
    assert sell.price == Decimal(10)
