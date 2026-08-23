"""Gifts of shares to someone other than a spouse: disposals at market value.

The consideration is the market value on the day (TCGA 1992 s17), and the
disposal is identified like a sale. A loss is reported on its own and kept
out of the loss total, since a loss on a gift to a connected person can
only be set against gains on disposals to that person (s18(3)).
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.exceptions import (
    CalculationError,
    InvalidTransactionError,
    PriceMissingError,
    UnclassifiedGiftError,
)
from cgt_calc.model import ActionType, BrokerTransaction, RuleType

from .calc_test_data import GBP, transaction, transfer_to_spouse_transaction
from .test_calc import create_calculator, get_report

BUY_DAY = datetime.date(2024, 6, 1)
GIFT_DAY = datetime.date(2024, 6, 10)


def _gift(quantity: int, price: float | None, fees: float = 0) -> BrokerTransaction:
    return transaction(
        GIFT_DAY, ActionType.GIFT, "FOO", quantity, price, fees, None, GBP
    )


def test_a_gift_with_a_gain_counts_like_a_sale() -> None:
    """Four shares costing £40 given away when worth £120: £80 of gain."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 30),
        ],
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(120)
    assert report.allowable_costs == Decimal(40)
    assert report.total_gain() == Decimal(80)
    assert "gift$FOO" in report.calculation_log[GIFT_DAY]
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


def test_a_loss_on_a_gift_is_reported_but_not_totalled() -> None:
    """Worth £20 when given, costing £40: a £20 loss, listed on its own."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 5),
        ],
    )

    # It is a disposal, and its proceeds and cost are reported as such.
    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(20)
    assert report.allowable_costs == Decimal(40)
    # But the loss does not reduce anything.
    assert report.capital_loss == Decimal(0)
    assert report.total_gain() == Decimal(0)
    text = str(report)
    assert "Gifts at market value" in text
    assert "loss £20.00 (not in totals)" in text


def test_a_gift_is_identified_like_a_sale() -> None:
    """A repurchase within 30 days is matched first, as for any disposal."""
    rebuy_day = datetime.date(2024, 6, 20)
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 30),
            transaction(rebuy_day, ActionType.BUY, "FOO", 4, 25, 0, -100, GBP),
        ],
    )

    entries = report.calculation_log[GIFT_DAY]["gift$FOO"]
    assert [e.rule_type for e in entries] == [RuleType.BED_AND_BREAKFAST]
    # Matched to the £25 repurchase, not the £10 pool.
    assert report.allowable_costs == Decimal(100)
    assert report.total_gain() == Decimal(20)


def test_a_fee_on_a_gift_is_allowed_as_a_cost() -> None:
    """£120 of value, £40 of cost, £5 to make the gift: £75 of gain."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(4, 30, fees=5),
        ],
    )

    assert report.disposal_proceeds == Decimal(120)
    assert report.allowable_costs == Decimal(45)
    assert report.total_gain() == Decimal(75)


@pytest.mark.parametrize(
    ("price", "error"),
    [(None, PriceMissingError), (0, InvalidTransactionError)],
)
def test_a_gift_needs_the_market_value(
    price: float | None, error: type[Exception]
) -> None:
    """The price column is the whole point of the row."""
    with pytest.raises(error):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                _gift(4, price),
            ],
        )


@pytest.mark.parametrize("gift_first", [True, False])
def test_a_sale_and_a_gift_on_one_day_are_refused(gift_first: bool) -> None:
    """One disposal under s105, but a loss on it cannot be split for s18(3)."""
    sale = transaction(GIFT_DAY, ActionType.SELL, "FOO", 2, 30, 0, 60, GBP)
    gift = _gift(2, 30)
    with pytest.raises(CalculationError, match="sale and a gift"):
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                *([gift, sale] if gift_first else [sale, gift]),
            ],
        )


def _unclassified(quantity: int, ambiguous: Decimal | None = None) -> BrokerTransaction:
    return BrokerTransaction(
        date=GIFT_DAY,
        action=ActionType.UNCLASSIFIED_GIFT,
        symbol="FOO",
        description="",
        quantity=Decimal(quantity),
        price=None,
        fees=Decimal(0),
        amount=Decimal(0),
        currency=GBP,
        broker="Testing",
        ambiguous_quantity=ambiguous,
    )


def test_a_gift_row_classifies_a_broker_gift() -> None:
    """The broker's row is accounted for by the GIFT row, and dropped."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _unclassified(4),
            _gift(4, 30),
        ],
    )

    assert report.total_gain() == Decimal(80)
    # Four shares left once, not twice.
    assert calculator.portfolio["FOO"].quantity == Decimal(6)


@pytest.mark.parametrize("confirmed", [2, 40])
def test_either_reading_of_an_ambiguous_broker_gift_can_be_a_gift(
    confirmed: int,
) -> None:
    """The GIFT row settles the count as well as the recipient."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 100, 10, 0, -1000, GBP),
            _unclassified(2, Decimal(40)),
            _gift(confirmed, 30),
        ],
    )

    assert calculator.portfolio["FOO"].quantity == Decimal(100 - confirmed)


def test_the_refusal_offers_the_gift_row_too() -> None:
    """Unclassified, the error shows both lines the user could add."""
    with pytest.raises(UnclassifiedGiftError) as err:
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
                _unclassified(4),
            ],
        )

    message = str(err.value)
    assert "2024-06-10,TRANSFER_TO_SPOUSE,FOO,4,0.00,0.00,GBP" in message
    assert "2024-06-10,GIFT,FOO,4,<market value per share>,0.00,GBP" in message


def test_a_gift_and_a_transfer_to_spouse_on_one_day_are_kept_apart() -> None:
    """Different recipients, both out of the pool: no clash to refuse."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            _gift(2, 30),
            transfer_to_spouse_transaction(GIFT_DAY, "FOO", 2),
        ],
    )

    assert report.total_gain() == Decimal(40)
    assert calculator.portfolio["FOO"].quantity == Decimal(6)
