"""Unit and integration tests."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.const import BALANCE_CHECK_CONTEXT_ROWS, RENAME_DESCRIPTION_PREFIX
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError, InvalidTransactionError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator, main
from cgt_calc.model import ActionType, BrokerTransaction, RuleType
from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.spin_off_handler import SpinOffHandler
from cgt_calc.util import round_decimal
from tests.utils import build_cmd

from .calc_test_data import (
    buy_transaction,
    calc_basic_data,
    split_transaction,
    transaction,
    transfer_transaction,
)
from .calc_test_data_2 import calc_basic_data_2

if TYPE_CHECKING:
    import argparse

    from cgt_calc.model import CalculationLog, CapitalGainsReport


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


ERI_ISIN = "US5949181045"


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
        "Parsing tests/trading212/data/2020/from_2020-09-11_to_2021-04-02.csv...",
        "Loaded 8 transactions from Trading 212",
        "Parsing tests/morgan_stanley/data/Releases Report.csv...",
        "Parsing tests/morgan_stanley/data/Withdrawals Report.csv...",
        "Loaded 6 transactions from Morgan Stanley",
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
