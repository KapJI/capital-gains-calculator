"""Unit and integration tests."""
from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.dates import date_to_index
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CalculationEntry,
    CalculationLog,
    CapitalGainsReport,
    RuleType,
)
from cgt_calc.util import round_decimal


def get_report(
    calculator: CapitalGainsCalculator, broker_transactions: list[BrokerTransaction]
) -> CapitalGainsReport:
    """Get calculation report."""
    acquisition_list, disposal_list = calculator.convert_to_hmrc_transactions(
        broker_transactions
    )
    return calculator.calculate_capital_gain(acquisition_list, disposal_list)


def buy_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
) -> BrokerTransaction:
    """Create buy transaction."""
    return BrokerTransaction(
        date,
        ActionType.BUY,
        symbol,
        f"Description for symbol {symbol}",
        round_decimal(Decimal(quantity), 6),
        round_decimal(Decimal(price), 6),
        round_decimal(Decimal(fees), 6),
        round_decimal(Decimal(amount), 6),
        "USD",
        "Testing",
    )


def sell_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
) -> BrokerTransaction:
    """Create sell transaction."""
    return BrokerTransaction(
        date,
        ActionType.SELL,
        symbol,
        f"Description for symbol {symbol}",
        round_decimal(Decimal(quantity), 6),
        round_decimal(Decimal(price), 6),
        round_decimal(Decimal(fees), 6),
        round_decimal(Decimal(amount), 6),
        "USD",
        "Testing",
    )


def transfer_transaction(
    date: datetime.date,
    amount: float,
    fees: float = 0,
) -> BrokerTransaction:
    """Create transfer transaction."""
    return BrokerTransaction(
        date,
        ActionType.TRANSFER,
        symbol=None,
        description="Test Transfer",
        quantity=None,
        price=None,
        fees=round_decimal(Decimal(fees), 6),
        amount=round_decimal(Decimal(amount), 6),
        currency="USD",
        broker="Testing",
    )


test_basic_data = [
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=5, year=2020), 5000),
            buy_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="FOO",
                quantity=3,
                price=5,
                fees=1,
                amount=-16,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="FOO",
                quantity=3,
                price=6,
                fees=1,
                amount=17,
            ),
        ],
        1.00,  # Expected capital gain/loss
        None,
        {
            date_to_index(datetime.date(day=1, month=5, year=2020)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(3),
                        amount=Decimal(-16),
                        allowable_cost=Decimal(16),
                        fees=Decimal(1),
                        new_quantity=Decimal(3),
                        new_pool_cost=Decimal(16),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(3),
                        amount=Decimal(17),
                        gain=Decimal(1),
                        allowable_cost=Decimal(16),
                        fees=Decimal(1),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
        },
        id="same_day_gain",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=4, year=2014), 6280),
            buy_transaction(
                date=datetime.date(day=1, month=4, year=2014),
                symbol="LOB",
                quantity=1000,
                price=4,
                fees=150,
                amount=-4150,
            ),
            buy_transaction(
                date=datetime.date(day=1, month=9, year=2017),
                symbol="LOB",
                quantity=500,
                price=4.1,
                fees=80,
                amount=-2130,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="LOB",
                quantity=700,
                price=4.8,
                fees=100,
                amount=3260,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=2, year=2021),
                symbol="LOB",
                quantity=400,
                price=5.2,
                fees=105,
                amount=1975,
            ),
        ],
        # exact amount would be £629+2/3
        629.66,  # Expected capital gain/loss
        None,
        {
            date_to_index(datetime.date(day=1, month=5, year=2020)): {
                "sell$LOB": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(700),
                        amount=Decimal(3260),
                        gain=Decimal("329.3333"),
                        allowable_cost=Decimal("2930.6667"),
                        fees=Decimal(100),
                        new_quantity=Decimal(800),
                        new_pool_cost=Decimal("3349.3333"),
                    ),
                ],
            },
            date_to_index(datetime.date(day=1, month=2, year=2021)): {
                "sell$LOB": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(400),
                        amount=Decimal(1975),
                        gain=Decimal("300.3333"),
                        allowable_cost=Decimal("1674.6667"),
                        fees=Decimal(105),
                        new_quantity=Decimal(400),
                        new_pool_cost=Decimal("1674.6667"),
                    ),
                ],
            },
        },
        # https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/972646/HS284_Example_3_2021.pdf
        id="HS284_Example_3_2021",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2019), 15100),
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2019),
                symbol="MSP",
                quantity=9500,
                price=1.5,
                fees=0,
                amount=-14250,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=8, year=2020),
                symbol="MSP",
                quantity=4000,
                price=1.5,
                fees=0,
                amount=6000,
            ),
            buy_transaction(
                date=datetime.date(day=11, month=9, year=2020),
                symbol="MSP",
                quantity=500,
                price=1.7,
                fees=0,
                amount=-850,
            ),
        ],
        -100,  # Expected capital gain/loss
        None,
        {
            date_to_index(datetime.date(day=30, month=8, year=2020)): {
                "sell$MSP": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(500),
                        amount=Decimal(750),
                        gain=Decimal(-100),
                        allowable_cost=Decimal(850),
                        fees=Decimal(0),
                        new_quantity=Decimal(9000),
                        new_pool_cost=Decimal(13500),
                        bed_and_breakfast_date_index=date_to_index(
                            datetime.date(day=11, month=9, year=2020)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(3500),
                        amount=Decimal(5250),
                        gain=Decimal(0),
                        allowable_cost=Decimal(5250),
                        fees=Decimal(0),
                        new_quantity=Decimal(5500),
                        new_pool_cost=Decimal(8250),
                    ),
                ],
            },
            date_to_index(datetime.date(day=11, month=9, year=2020)): {
                "buy$MSP": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(500),
                        amount=Decimal(-750),
                        allowable_cost=Decimal(850),
                        fees=Decimal(0),
                        new_quantity=Decimal(6000),
                        new_pool_cost=Decimal(9000),
                    ),
                ],
            },
        },
        # https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet/
        id="HS284_Example_2_2021",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=3, year=2021), 6782),
            buy_transaction(
                date=datetime.date(day=2, month=3, year=2021),
                symbol="FOO",
                quantity=100,
                price=25,
                fees=6,
                amount=-2506,
            ),
            buy_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=154,
                price=27.7,
                fees=10,
                amount=-4275.8,
            ),
            sell_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=254,
                price=28.03,
                fees=15,
                amount=7104.62,
            ),
            buy_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=28,
                fees=5,
                amount=-2525,
            ),
            sell_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=27,
                fees=5,
                amount=2425,
            ),
        ],
        222.82,  # Expected capital gain/loss
        None,
        {
            date_to_index(datetime.date(day=2, month=3, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(-2506),
                        allowable_cost=Decimal(2506),
                        fees=Decimal(6),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                ],
            },
            date_to_index(datetime.date(day=3, month=3, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(154),
                        amount=Decimal("-4275.8"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal(10),
                        new_quantity=Decimal(254),
                        new_pool_cost=Decimal("6781.8"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(154),
                        amount=Decimal("4307.5255"),
                        gain=Decimal("31.7255"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal("2797.0945"),
                        gain=Decimal("291.0945"),
                        allowable_cost=Decimal(2506),
                        fees=Decimal("5.9055"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            date_to_index(datetime.date(day=6, month=3, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(90),
                        amount=Decimal(-2525),
                        allowable_cost=Decimal(2525),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal(2525),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(90),
                        amount=Decimal(2425),
                        gain=Decimal(-100),
                        allowable_cost=Decimal(2525),
                        fees=Decimal(5),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
        },
        # Complex case when same day rule should be applied before bed & breakfast.
        id="bed_and_breakfast_vs_same_day",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=3, year=2021), 6782),
            buy_transaction(
                date=datetime.date(day=2, month=3, year=2021),
                symbol="FOO",
                quantity=100,
                price=25,
                fees=6,
                amount=-2506,
            ),
            buy_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=154,
                price=27.7,
                fees=10,
                amount=-4275.8,
            ),
            sell_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=254,
                price=28.03,
                fees=15,
                amount=7104.62,
            ),
            buy_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=28,
                fees=5,
                amount=-2525,
            ),
            sell_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=27,
                fees=5,
                amount=2425,
            ),
            buy_transaction(
                date=datetime.date(day=2, month=4, year=2021),
                symbol="FOO",
                quantity=30.5,
                price=30.2,
                fees=4,
                amount=-925.1,
            ),
        ],
        62.05,  # Expected capital gain/loss
        None,
        {
            date_to_index(datetime.date(day=2, month=3, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(-2506),
                        allowable_cost=Decimal(2506),
                        fees=Decimal(6),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                ],
            },
            date_to_index(datetime.date(day=3, month=3, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(154),
                        amount=Decimal("-4275.8"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal(10),
                        new_quantity=Decimal(254),
                        new_pool_cost=Decimal("6781.8"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(154),
                        amount=Decimal("4307.5255"),
                        gain=Decimal("31.7255"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(30.5),
                        amount=Decimal("853.1138"),
                        gain=Decimal("-71.9862"),
                        allowable_cost=Decimal("925.1"),
                        fees=Decimal("1.8012"),
                        new_quantity=Decimal(69.5),
                        new_pool_cost=Decimal("1741.67"),
                        bed_and_breakfast_date_index=date_to_index(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(69.5),
                        amount=Decimal("1943.9807"),
                        gain=Decimal("202.3107"),
                        allowable_cost=Decimal("1741.67"),
                        fees=Decimal("4.1043"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            date_to_index(datetime.date(day=6, month=3, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(90),
                        amount=Decimal(-2525),
                        allowable_cost=Decimal(2525),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal(2525),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(90),
                        amount=Decimal(2425),
                        gain=Decimal(-100),
                        allowable_cost=Decimal(2525),
                        fees=Decimal(5),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            date_to_index(datetime.date(day=2, month=4, year=2021)): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(30.5),
                        amount=Decimal("-764.33"),
                        allowable_cost=Decimal("925.1"),
                        fees=Decimal(4),
                        new_quantity=Decimal(30.5),
                        new_pool_cost=Decimal("764.33"),
                    ),
                ],
            },
        },
        # Add real bed & breakfast entries.
        id="with_bed_and_breakfast",
    ),
]


@pytest.mark.parametrize(
    "tax_year,broker_transactions,expected,gbp_prices,calculation_log", test_basic_data
)
def test_basic(
    tax_year: int,
    broker_transactions: list[BrokerTransaction],
    expected: float,
    gbp_prices: dict[int, Decimal] | None,
    calculation_log: CalculationLog | None,
) -> None:
    """Generate basic tests for test data."""
    if gbp_prices is None:
        gbp_prices = {
            date_to_index(t.date.replace(day=1)): Decimal(1)
            for t in broker_transactions
        }
    converter = CurrencyConverter(gbp_prices)
    initial_prices = InitialPrices({})
    calculator = CapitalGainsCalculator(tax_year, converter, initial_prices)
    report = get_report(calculator, broker_transactions)
    print(report)
    assert report.total_gain() == round_decimal(Decimal(expected), 2)
    if calculation_log is not None:
        result_log = report.calculation_log
        assert len(result_log) == len(calculation_log)
        for date_index, expected_entries_map in calculation_log.items():
            assert date_index in result_log
            result_entries_map = result_log[date_index]
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


def test_run_with_example_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = [
        "poetry",
        "run",
        "cgt-calc",
        "--year",
        "2020",
        "--schwab",
        "tests/test_data/schwab_transactions.csv",
        "--trading212",
        "tests/test_data/trading212/",
        "--mssb",
        "tests/test_data/mssb/",
        "--no-pdflatex",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    assert result.stderr == b"", "Run with example files generated errors"
    expected_file = (
        Path("tests") / "test_data" / "test_run_with_example_files_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout.decode("utf-8") == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
