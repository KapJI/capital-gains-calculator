"""Additional tests for calc."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.model import CalculationEntry, RuleType
from tests.test_data.calc_test_data import buy_transaction, transfer_transaction

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
]
