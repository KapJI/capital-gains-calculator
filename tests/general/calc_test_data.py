"""Additional tests for calc."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.model import ActionType, BrokerTransaction, CalculationEntry, RuleType
from cgt_calc.util import round_decimal


def interest_transaction(
    date: datetime.date,
    amount: float,
    currency: str = "USD",
) -> BrokerTransaction:
    """Create interest transaction."""
    return transaction(date, ActionType.INTEREST, None, None, None, 0, amount, currency)


def dividend_transaction(
    date: datetime.date,
    symbol: str,
    amount: float,
) -> BrokerTransaction:
    """Create dividend transaction."""
    return transaction(
        date,
        ActionType.DIVIDEND,
        symbol,
        None,
        None,
        0,
        amount,
    )


def dividend_tax_transaction(
    date: datetime.date,
    symbol: str,
    amount: float,
) -> BrokerTransaction:
    """Create dividend tax transaction."""
    return transaction(
        date,
        ActionType.DIVIDEND_TAX,
        symbol,
        None,
        None,
        0,
        -amount,
    )


def eri_transaction(
    date: datetime.date,
    isin: str,
    price: float,
) -> BrokerTransaction:
    """Create excess reported income transaction."""
    return transaction(date, ActionType.EXCESS_REPORTED_INCOME, price=price, isin=isin)


def buy_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
    isin: str | None = None,
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
        isin=isin,
    )


def sell_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
    isin: str | None = None,
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
        isin=isin,
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
    currency: str = "USD",
    isin: str | None = None,
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
        currency=currency,
        broker="Testing",
        isin=isin,
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
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
                        allowable_cost=Decimal(16) + Decimal(1),
                        fees=Decimal(1),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=1, month=5, year=2020): {
                "sell$LOB": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(700),
                        amount=Decimal(3260),
                        gain=Decimal("329.3333"),
                        allowable_cost=Decimal("2930.6667") + Decimal(100),
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
                        allowable_cost=Decimal("1674.6667") + Decimal(105),
                        fees=Decimal(105),
                        new_quantity=Decimal(400),
                        new_pool_cost=Decimal("1674.6667"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
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
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
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
                        allowable_cost=Decimal("4275.8") + Decimal("9.0945"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal("2797.0945"),
                        gain=Decimal("291.0945"),
                        allowable_cost=Decimal(2506) + Decimal("5.9055"),
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
                        allowable_cost=Decimal(2525) + Decimal(5),
                        fees=Decimal(5),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
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
                        allowable_cost=Decimal("4275.8") + Decimal("9.0945"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("30.5"),
                        amount=Decimal("853.1138"),
                        gain=Decimal("-71.9862"),
                        allowable_cost=Decimal("925.1") + Decimal("1.8012"),
                        fees=Decimal("1.8012"),
                        new_quantity=Decimal("69.5"),
                        new_pool_cost=Decimal("1741.67"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("69.5"),
                        amount=Decimal("1943.9807"),
                        gain=Decimal("202.3107"),
                        allowable_cost=Decimal("1741.67") + Decimal("4.1043"),
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
                        allowable_cost=Decimal(2525) + Decimal(5),
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
                        quantity=Decimal("30.5"),
                        amount=Decimal("-764.33"),
                        allowable_cost=Decimal("925.1"),
                        fees=Decimal(4),
                        new_quantity=Decimal("30.5"),
                        new_pool_cost=Decimal("764.33"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
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
                        allowable_cost=Decimal("4275.8") + Decimal("9.0945"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(90),
                        amount=Decimal("2517.3850"),
                        gain=Decimal("-7.6150"),
                        allowable_cost=Decimal("2525.0") + Decimal("5.3150"),
                        fees=Decimal("5.3150"),
                        new_quantity=Decimal(10),
                        new_pool_cost=Decimal("250.60"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=5, month=3, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("10.0"),
                        amount=Decimal("279.7094488188976377952755906"),
                        gain=Decimal("-23.6020265909384277784949012"),
                        allowable_cost=Decimal("303.3114754098360655737704918")
                        + Decimal("0.5905511811023622047244094488"),
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
                        amount=Decimal("-2255.4000"),
                        allowable_cost=Decimal("2525.0000"),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal("2255.4000"),
                    ),
                ],
            },
            datetime.date(day=6, month=3, year=2021): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("20.5"),
                        amount=Decimal("552.3611"),
                        gain=Decimal("-69.4274"),
                        allowable_cost=Decimal("621.7885") + Decimal("1.13888"),
                        fees=Decimal("1.13888"),
                        new_quantity=Decimal("69.5"),
                        new_pool_cost=Decimal("1741.6700"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("69.5"),
                        amount=Decimal("1872.6389"),
                        gain=Decimal("130.9689"),
                        allowable_cost=Decimal("1741.67") + Decimal("3.8611"),
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
                        quantity=Decimal("30.5"),
                        amount=Decimal("-764.33"),
                        allowable_cost=Decimal("925.1"),
                        fees=Decimal(4),
                        new_quantity=Decimal("30.5"),
                        new_pool_cost=Decimal("764.33"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=25, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("30.0"),
                        amount=Decimal(2999),
                        gain=Decimal("-34.06"),
                        allowable_cost=Decimal("3033.06") + Decimal("1.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                ],
            },
            datetime.date(day=30, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("50.0"),
                        amount=Decimal(-4951),
                        gain=Decimal(0),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(520),
                        new_pool_cost=Decimal("52468.94"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal("50.0"),
                        amount=Decimal("4999.50"),
                        gain=Decimal("48.5"),
                        allowable_cost=Decimal(4951) + Decimal("0.5"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("50.0"),
                        amount=Decimal("4999.5"),
                        gain=Decimal("-55.6"),
                        allowable_cost=Decimal("5055.1") + Decimal("0.5"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal("42462.84"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
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
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=25, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("30.0"),
                        amount=Decimal(2999),
                        gain=Decimal("28.4"),
                        allowable_cost=Decimal("2970.6") + Decimal("1.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
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
                        quantity=Decimal("50.0"),
                        amount=Decimal(-4951),
                        gain=Decimal(0),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(520),
                        new_pool_cost=Decimal("52468.94"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal("50.0"),
                        amount=Decimal("4999.50"),
                        gain=Decimal("48.5"),
                        allowable_cost=Decimal(4951) + Decimal("0.5"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("20.0"),
                        amount=Decimal("1999.8"),
                        gain=Decimal("19.4"),
                        allowable_cost=Decimal("1980.4") + Decimal("0.2"),
                        fees=Decimal("0.2"),
                        new_quantity=Decimal(450),
                        new_pool_cost=Decimal("45495.9"),
                        bed_and_breakfast_date_index=datetime.date(
                            day=1, month=7, year=2023
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("30.0"),
                        amount=Decimal("2999.7"),
                        gain=Decimal("-33.36"),
                        allowable_cost=Decimal("3033.06") + Decimal("0.3"),
                        fees=Decimal("0.3"),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal("42462.84"),
                    ),
                ],
            },
            datetime.date(day=1, month=7, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("50.0"),
                        amount=Decimal("-5055.1"),
                        gain=Decimal(0),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="sell_on_30/6_is_split_into_104+same_day+b&d",
    ),
]
