"""What a day put into a holding stays sorted by how it got there.

The acquisition log used to add a purchase, a spin-off's shares and a
management fee into one figure per day and ticker, and matching worked out
what it was made of by subtracting the parts it could find recorded
elsewhere. These pin what each part is recorded as, so a later change can ask
for the day's genuine acquisitions directly.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from cgt_calc.model import (
    AcquisitionLog,
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    HmrcTransactionData,
)

from .calc_test_data import GBP, transaction
from .test_calc import _gbp_fee, _gbp_trade, create_calculator, get_report
from .test_spin_off_basis import BUY_DAY, FLAT, SPIN_OFF_DAY, spin_off
from .test_stock_splits import legacy_split

DAY = datetime.date(2024, 5, 10)
EARLIER_DAY = datetime.date(2024, 5, 1)


def _ledger(transactions: list[BrokerTransaction]) -> AcquisitionLog:
    """Return the acquisition log a run of these transactions records."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    get_report(calculator, transactions)
    return calculator.state.history.acquisition_list


def test_a_day_s_purchase_and_spin_off_stay_separately_recorded() -> None:
    """The five bought and the ten spun off are two records, not fifteen units.

    A tenth of FOO's value goes to BAR, so £10 of its £100 carries across,
    beside the £50 paid for five more.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 5, 10, 0, -50, GBP),
        ],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )

    record = calculator.state.history.acquisition_list[SPIN_OFF_DAY]["BAR"]
    assert record.purchased.quantity == Decimal(5)
    assert record.purchased.amount == Decimal(50)
    assert record.reorganised.quantity == Decimal(10)
    assert record.reorganised.amount == Decimal(10)
    assert record.cost_only == HmrcTransactionData()
    # The blend they used to be, which is still what the pool moves by.
    assert record.total.quantity == Decimal(15)
    assert record.total.amount == Decimal(60)


def test_a_management_fee_is_recorded_as_cost_with_no_shares() -> None:
    """A fee brings in cost that no shares of its own carry."""
    record = _ledger(
        [
            _gbp_trade(EARLIER_DAY, ActionType.BUY, "X", 100, 1000),
            _gbp_fee(DAY, "X", 40),
        ]
    )[DAY]["X"]

    assert record.cost_only.amount == Decimal(40)
    assert record.cost_only.quantity == Decimal(0)
    assert record.purchased == HmrcTransactionData()
    assert record.reorganised == HmrcTransactionData()
    assert record.total.quantity == Decimal(0)


def test_a_purchase_and_a_fee_on_one_day_stay_separately_recorded() -> None:
    """A purchase under the same name no longer hides the fee.

    Both used to add into one record whose quantity a fee cannot change, so
    the only sign of the fee was a total priced above what the shares cost.
    """
    record = _ledger(
        [
            _gbp_trade(EARLIER_DAY, ActionType.BUY, "X", 100, 1000),
            _gbp_trade(DAY, ActionType.BUY, "X", 50, 750),
            _gbp_fee(DAY, "X", 100),
        ]
    )[DAY]["X"]

    assert record.purchased.quantity == Decimal(50)
    assert record.purchased.amount == Decimal(750)
    assert record.cost_only.amount == Decimal(100)
    assert record.total.amount == Decimal(850)


def test_a_vest_and_a_transfer_from_a_spouse_are_recorded_as_purchases() -> None:
    """Neither is a reorganisation, so both stay identifiable acquisitions.

    A vest is an acquisition at market value, and shares from a spouse arrive
    at the base cost they left with (TCGA 1992 s58, CG22200).
    """
    ledger = _ledger(
        [
            transaction(DAY, ActionType.STOCK_ACTIVITY, "VEST", 10, 10, 0, None, GBP),
            transaction(
                DAY,
                ActionType.TRANSFER_FROM_SPOUSE,
                "GIFTED",
                40,
                10,
                0,
                None,
                CurrencyCode(GBP),
            ),
        ]
    )

    assert ledger[DAY]["VEST"].purchased.quantity == Decimal(10)
    assert ledger[DAY]["VEST"].purchased.amount == Decimal(100)
    assert ledger[DAY]["VEST"].reorganised == HmrcTransactionData()
    assert ledger[DAY]["GIFTED"].purchased.quantity == Decimal(40)
    assert ledger[DAY]["GIFTED"].purchased.amount == Decimal(400)
    assert ledger[DAY]["GIFTED"].reorganised == HmrcTransactionData()


def test_a_reorganisation_stated_as_a_share_count_records_no_acquisition() -> None:
    """A split restates a holding, so it puts nothing into the day's record.

    Only a spin-off reaches ``reorganised``: it names a holding of its own and
    carries cost into it, where a split leaves the same pool under the same
    name.
    """
    split_day = datetime.date(2023, 5, 15)
    ledger = _ledger(
        [
            _gbp_trade(datetime.date(2023, 5, 2), ActionType.BUY, "FOO", 100, 1000),
            legacy_split(split_day, "FOO", "100"),
        ]
    )

    assert split_day not in ledger
