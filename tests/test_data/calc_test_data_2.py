"""Additional tests for calc."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.model import ActionType, CalculationEntry, RuleType, SpinOff
from tests.test_data.calc_test_data import (
    buy_transaction,
    sell_transaction,
    transaction,
    transfer_transaction,
)

calc_basic_data_2 = [
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=5, year=2023), 5000),
            buy_transaction(
                date=datetime.date(day=1, month=5, year=2023),
                symbol="FOO",
                quantity=3,
                price=5,
                fees=1,
                amount=-16,
            ),
        ],
        0.0,  # Expected capital gain/loss
        2.0,  # Expected unrealized gains
        None,  # GBP/USD prices
        {"FOO": 6},  # Current prices
        {
            datetime.date(day=1, month=5, year=2023): {
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
            },
        },
        id="unrealized_gains_test",
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
            transaction(
                date=datetime.date(day=5, month=7, year=2023),
                action_type=ActionType.SPIN_OFF,
                symbol="BAR",
                quantity=840,  # 1:2 spin_off
                price=10,
                fees=1,
            ),
            buy_transaction(
                date=datetime.date(day=10, month=7, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=8, year=2023),
                symbol="BAR",
                quantity=800,
                price=10,
                fees=1,
                amount=7999,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=8, year=2023),
                symbol="FOO",
                quantity=100,
                price=110,
                fees=1,
                amount=10999,
            ),
        ],
        2557.10,  # Expected capital gain/loss
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
                            day=10, month=7, year=2023
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
                        quantity=Decimal(50),
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
                            day=10, month=7, year=2023
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
            datetime.date(day=5, month=7, year=2023): {
                "buy$BAR": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(840),
                        amount=Decimal(-8948.21),
                        gain=Decimal(0),
                        allowable_cost=Decimal(8948.21),
                        fees=Decimal(1),
                        new_quantity=Decimal(840),
                        new_pool_cost=Decimal(8948.21),
                        spin_off=SpinOff(
                            cost_proportion=Decimal(0.1),
                            source="FOO",
                            dest="BAR",
                            date=datetime.date(day=5, month=7, year=2023),
                        ),
                    ),
                ],
                "spin-off$FOO": [
                    CalculationEntry(
                        RuleType.SPIN_OFF,
                        quantity=Decimal(470),
                        amount=Decimal(-47517.94),
                        gain=Decimal(0),
                        allowable_cost=Decimal(37514.1632),
                        fees=Decimal(0),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(37514.1632),
                        spin_off=SpinOff(
                            cost_proportion=Decimal(0.1),
                            source="FOO",
                            dest="BAR",
                            date=datetime.date(day=5, month=7, year=2023),
                        ),
                    ),
                ],
            },
            datetime.date(day=10, month=7, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(50.0),
                        amount=Decimal(-5055.1),
                        gain=Decimal(0),
                        allowable_cost=Decimal(4951),
                        fees=Decimal(1),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal(47517.94),
                    ),
                ],
            },
            datetime.date(day=30, month=8, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(10999),
                        gain=Decimal("3017.2632"),
                        allowable_cost=Decimal("7981.7368"),
                        fees=Decimal(1),
                        new_quantity=Decimal(370),
                        new_pool_cost=Decimal(29532.4263),
                    ),
                ],
                "sell$BAR": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(800),
                        amount=Decimal(7999),
                        gain=Decimal("-523.1048"),
                        allowable_cost=Decimal("8522.1048"),
                        fees=Decimal(1),
                        new_quantity=Decimal(40),
                        new_pool_cost=Decimal(426.1052),
                    ),
                ],
            },
        },
        id="spin_off",
    ),
]
