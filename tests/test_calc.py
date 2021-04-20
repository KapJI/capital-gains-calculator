"""Unit and integration tests."""
import datetime
from decimal import Decimal
import os
import subprocess
from typing import Dict, List, Optional

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.dates import date_to_index
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CapitalGainsReport
from cgt_calc.util import round_decimal


def get_report(
    calculator: CapitalGainsCalculator, broker_transactions: List[BrokerTransaction]
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
        Decimal(quantity),
        Decimal(price),
        Decimal(fees),
        Decimal(amount),
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
        Decimal(quantity),
        Decimal(price),
        Decimal(fees),
        Decimal(amount),
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
        fees=Decimal(fees),
        amount=Decimal(amount),
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
                price=5.0,
                fees=1.0,
                amount=-16.0,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="FOO",
                quantity=3,
                price=6.0,
                fees=1.0,
                amount=17.0,
            ),
        ],
        1.00,  # Expected capital gain/loss
        None,
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
                fees=150.0,
                amount=-4150.0,
            ),
            buy_transaction(
                date=datetime.date(day=1, month=9, year=2017),
                symbol="LOB",
                quantity=500,
                price=4.1,
                fees=80.0,
                amount=-2130.0,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="LOB",
                quantity=700,
                price=4.8,
                fees=100.0,
                amount=3260.0,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=2, year=2021),
                symbol="LOB",
                quantity=400,
                price=5.2,
                fees=105.0,
                amount=1975.0,
            ),
        ],
        # exact amount would be £629+2/3
        629.66,  # Expected capital gain/loss
        None,
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
                fees=0.0,
                amount=-14250.0,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=8, year=2020),
                symbol="MSP",
                quantity=4000,
                price=1.5,
                fees=0.0,
                amount=6000.0,
            ),
            buy_transaction(
                date=datetime.date(day=11, month=9, year=2020),
                symbol="MSP",
                quantity=500,
                price=1.7,
                fees=0.0,
                amount=-850.0,
            ),
        ],
        -100,  # Expected capital gain/loss
        None,
        # https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet/
        id="HS284_Example_2_2021",
    ),
]


@pytest.mark.parametrize(
    "tax_year,broker_transactions,expected,gbp_prices", test_basic_data
)
def test_basic(
    tax_year: int,
    broker_transactions: List[BrokerTransaction],
    expected: float,
    gbp_prices: Optional[Dict[int, Decimal]],
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
        "--report",
        "",
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    assert result.stderr == b"", "Run with example files generated errors"
    expected_file = os.path.join("tests", "test_run_with_example_files_output.txt")
    with open(expected_file, "r") as file:
        expected = file.read()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout.decode("utf-8") == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
