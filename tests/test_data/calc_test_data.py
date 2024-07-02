"""Additional tests for calc."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.model import ActionType, BrokerTransaction, CalculationEntry, RuleType
from cgt_calc.util import round_decimal


def buy_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
) -> BrokerTransaction:
    """Create buy transaction."""
    return transaction(
        date,
        ActionType.BUY,
        symbol,
        quantity,
        price,
        fees,
        amount,
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
    return transaction(
        date,
        ActionType.SELL,
        symbol,
        quantity,
        price,
        fees,
        amount,
    )


def transfer_transaction(
    date: datetime.date,
    amount: float,
    fees: float = 0,
) -> BrokerTransaction:
    """Create transfer transaction."""
    return transaction(
        date,
        ActionType.TRANSFER,
        fees=fees,
        amount=amount,
    )


def transaction(
    date: datetime.date,
    action_type: ActionType,
    symbol: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    fees: float = 0.0,
    amount: float | None = None,
) -> BrokerTransaction:
    """Create transaction."""
    return BrokerTransaction(
        date,
        action_type,
        symbol=symbol,
        description=f"Description for symbol {symbol}",
        quantity=round_decimal(Decimal(quantity), 6) if quantity else None,
        price=round_decimal(Decimal(price), 6) if price else None,
        fees=round_decimal(Decimal(fees), 6),
        amount=round_decimal(Decimal(amount), 6) if amount else None,
        currency="USD",
        broker="Testing",
    )


def split_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
) -> BrokerTransaction:
    """Create sell transaction."""
    return BrokerTransaction(
        date,
        ActionType.STOCK_SPLIT,
        symbol,
        f"Split of {symbol}",
        round_decimal(Decimal(quantity), 6),
        price=Decimal(0),
        fees=Decimal(0),
        amount=Decimal(0),
        currency="USD",
        broker="Testing",
    )


calc_basic_data = [
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
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=1, month=5, year=2020): {
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
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=1, month=5, year=2020): {
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
            datetime.date(day=1, month=2, year=2021): {
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
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=30, month=8, year=2020): {
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
                        bed_and_breakfast_date_index=(
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
            datetime.date(day=11, month=9, year=2020): {
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
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=2, month=3, year=2021): {
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
            datetime.date(day=3, month=3, year=2021): {
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
            datetime.date(day=6, month=3, year=2021): {
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
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=2, month=3, year=2021): {
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
            datetime.date(day=3, month=3, year=2021): {
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
                        bed_and_breakfast_date_index=(
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
            datetime.date(day=6, month=3, year=2021): {
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
            datetime.date(day=2, month=4, year=2021): {
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
                date=datetime.date(day=5, month=3, year=2021),
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
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=2, month=3, year=2021): {
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
            datetime.date(day=3, month=3, year=2021): {
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
                        quantity=Decimal(90),
                        amount=Decimal("2517.3850"),
                        gain=Decimal("-7.6150"),
                        allowable_cost=Decimal("2525.0"),
                        fees=Decimal("5.3150"),
                        new_quantity=Decimal(10),
                        new_pool_cost=Decimal("250.60"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=5, month=3, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(10.0),
                        amount=Decimal("279.7094488188976377952755906"),
                        gain=Decimal("-23.6020265909384277784949012"),
                        allowable_cost=Decimal("303.3114754098360655737704918"),
                        fees=Decimal("0.5905511811023622047244094488"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                ],
            },
            datetime.date(day=5, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(90),
                        amount=Decimal(-2255.4000),
                        allowable_cost=Decimal(2525.0000),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal(2255.4000),
                    ),
                ],
            },
            datetime.date(day=6, month=3, year=2021): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(20.5),
                        amount=Decimal("552.3611"),
                        gain=Decimal("-69.4274"),
                        allowable_cost=Decimal("621.7885"),
                        fees=Decimal("1.13888"),
                        new_quantity=Decimal(69.5),
                        new_pool_cost=Decimal(1741.6700),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(69.5),
                        amount=Decimal("1872.6389"),
                        gain=Decimal("130.9689"),
                        allowable_cost=Decimal("1741.67"),
                        fees=Decimal("3.8611"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            datetime.date(day=2, month=4, year=2021): {
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
        id="with_bed_and_breakfast_2",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2023), 52503),
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2023),
                symbol="FOO",
                quantity=500,
                price=101.1,
                fees=1,
                amount=-50551,
            ),
            sell_transaction(
                date=datetime.date(day=25, month=6, year=2023),
                symbol="FOO",
                quantity=30,
                price=100,
                fees=1,
                amount=2999,
            ),
            buy_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=100,
                price=100,
                fees=1,
                amount=9999,
            ),
        ],
        -41.16,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=25, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(30.0),
                        amount=Decimal("2999"),
                        gain=Decimal("-34.06"),
                        allowable_cost=Decimal("3033.06"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(47517.94),
                    ),
                ],
            },
            datetime.date(day=30, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(50.0),
                        amount=Decimal("-4951"),
                        gain=Decimal("0"),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(520),
                        new_pool_cost=Decimal(52468.94),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(50.0),
                        amount=Decimal("4999.50"),
                        gain=Decimal("48.5"),
                        allowable_cost=Decimal("4951"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(47517.94),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(50.0),
                        amount=Decimal("4999.5"),
                        gain=Decimal("-55.6"),
                        allowable_cost=Decimal("5055.1"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal(42462.84),
                    ),
                ],
            },
        },
        id="issue_460.sell_on_30/6_is_split_into_104+same_day",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2023), 52503),
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2023),
                symbol="FOO",
                quantity=500,
                price=101.1,
                fees=1,
                amount=-50551,
            ),
            sell_transaction(
                date=datetime.date(day=25, month=6, year=2023),
                symbol="FOO",
                quantity=30,
                price=100,
                fees=1,
                amount=2999,
            ),
            buy_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=100,
                price=100,
                fees=1,
                amount=9999,
            ),
            buy_transaction(
                date=datetime.date(day=1, month=7, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
        ],
        62.94,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=25, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(30.0),
                        amount=Decimal("2999"),
                        gain=Decimal("28.4"),
                        allowable_cost=Decimal("2970.6"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(47517.94),
                        bed_and_breakfast_date_index=datetime.date(
                            day=1, month=7, year=2023
                        ),
                    ),
                ],
            },
            datetime.date(day=30, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(50.0),
                        amount=Decimal("-4951"),
                        gain=Decimal("0"),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(520),
                        new_pool_cost=Decimal(52468.94),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(50.0),
                        amount=Decimal("4999.50"),
                        gain=Decimal("48.5"),
                        allowable_cost=Decimal("4951"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(47517.94),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(20.0),
                        amount=Decimal("1999.8"),
                        gain=Decimal("19.4"),
                        allowable_cost=Decimal("1980.4"),
                        fees=Decimal("0.2"),
                        new_quantity=Decimal(450),
                        new_pool_cost=Decimal(45495.9),
                        bed_and_breakfast_date_index=datetime.date(
                            day=1, month=7, year=2023
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(30.0),
                        amount=Decimal("2999.7"),
                        gain=Decimal("-33.36"),
                        allowable_cost=Decimal("3033.06"),
                        fees=Decimal("0.3"),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal(42462.84),
                    ),
                ],
            },
            datetime.date(day=1, month=7, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(50.0),
                        amount=Decimal("-5055.1"),
                        gain=Decimal("0"),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(47517.94),
                    ),
                ],
            },
        },
        id="sell_on_30/6_is_split_into_104+same_day+b&d",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=5, year=2020), 100),
            buy_transaction(
                date=datetime.date(day=2, month=5, year=2023),
                symbol="FOO",
                quantity=12,
                price=5,
                amount=-60,
                fees=0,
            ),
            sell_transaction(
                date=datetime.date(day=10, month=5, year=2023),
                symbol="FOO",
                quantity=2,
                price=6,
                amount=12,  # 2.00 gain
                fees=0,
            ),
            split_transaction(
                date=datetime.date(day=15, month=5, year=2023),
                symbol="FOO",
                quantity=10,  # 2x split
            ),
            sell_transaction(
                date=datetime.date(day=10, month=6, year=2023),
                symbol="FOO",
                quantity=2,
                price=5,
                amount=10,  # 5.00 gain
                fees=0,
            ),
        ],
        7.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        {
            datetime.date(day=2, month=5, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(12),
                        amount=Decimal(-60),
                        allowable_cost=Decimal(60),
                        new_quantity=Decimal(12),
                        fees=Decimal(0),
                        new_pool_cost=Decimal(60),
                    ),
                ],
            },
            datetime.date(day=10, month=5, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(2),
                        amount=Decimal(12),
                        gain=Decimal(2),
                        allowable_cost=Decimal(10),
                        fees=Decimal(0),
                        new_quantity=Decimal(10),
                        new_pool_cost=Decimal(50),
                    ),
                ],
            },
            datetime.date(day=15, month=5, year=2023): {
                "split$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(10),
                        amount=Decimal(0),
                        gain=Decimal(0),
                        fees=Decimal(0),
                        allowable_cost=Decimal(0),
                        new_quantity=Decimal(20),
                        new_pool_cost=Decimal(50),
                    ),
                ],
            },
            datetime.date(day=10, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(2),
                        amount=Decimal(10),
                        gain=Decimal(5),
                        fees=Decimal(0),
                        allowable_cost=Decimal(5),
                        new_quantity=Decimal(18),
                        new_pool_cost=Decimal(45),
                    ),
                ],
            },
        },
        id="split",
    ),
]
