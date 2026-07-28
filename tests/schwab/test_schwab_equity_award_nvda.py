"""NVIDIA-specific parsing of Schwab Equity Award JSON exports.

Schwab reports NVDA history in mixed units. Acquisitions are restated for later
splits, disposals are not, and one record type mixes both inside itself. Each
test here pins one of those rules.

The fixture is a synthetic portfolio: real dates and real prices, invented
share counts. Real dates and prices because they are what is under test — the
split boundaries of 20 July 2021 and 10 June 2024, and the tax year end.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.model import ActionType
from cgt_calc.parsers import schwab_equity_award_json
from cgt_calc.parsers.schwab_equity_award_json import nvidia_split_multiplier

FIXTURE = Path("tests/schwab/data/equity_award/nvda_synthetic.json")


@pytest.fixture(name="transactions")
def transactions_fixture() -> list:
    """Parse the synthetic NVDA portfolio."""
    return schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
        FIXTURE
    )


def on(transactions: list, date: datetime.date, action: ActionType):
    """Return the single transaction of that kind on that date."""
    found = [t for t in transactions if t.date == date and t.action == action]
    assert len(found) == 1, f"expected one {action} on {date}, got {len(found)}"
    return found[0]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        # Before both splits: 4:1 in 2021 then 10:1 in 2024.
        (datetime.date(2021, 2, 26), 40),
        (datetime.date(2021, 7, 19), 40),
        # The new shares trade from the 20th, so that day is already post-split.
        (datetime.date(2021, 7, 20), 10),
        (datetime.date(2024, 6, 7), 10),
        (datetime.date(2024, 6, 10), 1),
        (datetime.date(2026, 4, 2), 1),
    ],
)
def test_split_multiplier_boundaries(day: datetime.date, expected: int) -> None:
    """The multiplier changes on the day the new shares trade."""
    assert nvidia_split_multiplier(day) == expected


def test_lapse_is_not_an_acquisition(transactions: list) -> None:
    """A Lapse duplicates its Deposit and must not reach the pool.

    Its NetSharesDeposited is in the units of the day while its price is
    restated, so pairing them puts a tenth of the shares in at the full price.
    """
    assert len(transactions) == 12  # 6 acquisitions, 4 disposals, 2 cash

    vests = [
        t
        for t in transactions
        if t.action == ActionType.STOCK_ACTIVITY
        and t.date == datetime.date(2021, 9, 15)
    ]
    assert len(vests) == 1
    # The paired Lapse says 40; the Deposit says 400 and is the one that counts.
    assert vests[0].quantity == Decimal(400)


def test_vest_is_read_in_restated_units(transactions: list) -> None:
    """Both the count and the price of a vest are already restated."""
    vest = on(transactions, datetime.date(2021, 9, 15), ActionType.STOCK_ACTIVITY)

    assert vest.quantity == Decimal(400)
    assert vest.price == Decimal("22.341")


def test_sell_to_cover_leaves_net_shares(transactions: list) -> None:
    """1000 vested, 300 went to tax, and the Deposit is already net."""
    vest = on(transactions, datetime.date(2024, 9, 18), ActionType.STOCK_ACTIVITY)

    assert vest.quantity == Decimal(700)


def test_espp_uses_market_value_not_price_paid(transactions: list) -> None:
    """The discount is taxed through payroll, so the basis is market value.

    Using PurchasePrice of $9.3588 instead of the $13.7145 the shares were
    worth understates the basis by a third on this purchase, and roughly
    tenfold on the ones where the discount is larger.
    """
    espp = on(transactions, datetime.date(2021, 2, 26), ActionType.STOCK_ACTIVITY)

    assert espp.quantity == Decimal(80)
    assert espp.price == Decimal("13.7145")
    assert espp.amount == Decimal(80) * Decimal("13.7145")


def test_espp_taxed_in_shares_pools_the_net(transactions: list) -> None:
    """Since August 2025 the tax is settled in shares: 200 bought, 150 kept."""
    espp = on(transactions, datetime.date(2026, 2, 27), ActionType.STOCK_ACTIVITY)

    assert espp.quantity == Decimal(150)
    assert espp.price == Decimal("177.19")


def test_disposal_before_the_split_is_converted(transactions: list) -> None:
    """Disposals are the one record left in the units of their own day.

    Two shares at $191.93 as recorded is twenty at $19.193 in current units,
    and the money is the same either way. Missing this costs 54 shares across
    the two 2023 disposals, and the pool stays wrong by that much for every
    year afterwards.
    """
    sale = on(transactions, datetime.date(2023, 1, 23), ActionType.SELL)

    assert sale.quantity == Decimal(20)
    assert sale.price == Decimal("19.193")
    assert sale.quantity * sale.price == Decimal("383.86")


def test_disposal_after_the_split_is_left_alone(transactions: list) -> None:
    """A disposal already in current units needs no conversion."""
    sale = on(transactions, datetime.date(2026, 4, 2), ActionType.SELL)

    assert sale.quantity == Decimal(120)
    assert sale.price == Decimal("176.725")


def test_disposal_price_comes_from_the_money(transactions: list) -> None:
    """Amount is net of fees, so the gross is rebuilt before dividing.

    Deriving the price this way rather than from the per-lot SalePrice is what
    lets a disposal span lots sold at different prices.
    """
    sale = on(transactions, datetime.date(2025, 5, 22), ActionType.SELL)

    assert sale.fees == Decimal("0.10")
    assert sale.price == Decimal("132.83")
    assert sale.quantity == Decimal(500)


def test_a_commission_free_disposal_has_zero_fees(transactions: list) -> None:
    """Schwab writes an empty string for the fees rather than a zero."""
    sale = on(transactions, datetime.date(2023, 1, 23), ActionType.SELL)

    assert sale.fees == Decimal("0.02")


def test_withholding_on_dividends_is_kept(transactions: list) -> None:
    """US tax at source, needed for SA106 and foreign tax credit relief.

    Skipping it alongside Lapse would lose the figure entirely.
    """
    withheld = [t for t in transactions if t.action == ActionType.DIVIDEND_TAX]

    assert len(withheld) == 1
    assert withheld[0].amount == Decimal("-2.22")


def test_the_position_matches_the_transactions(transactions: list) -> None:
    """Acquisitions less disposals, in current units throughout."""
    acquired = sum(
        t.quantity or Decimal(0)
        for t in transactions
        if t.action == ActionType.STOCK_ACTIVITY
    )
    disposed = sum(
        t.quantity or Decimal(0)
        for t in transactions
        if t.action == ActionType.SELL
    )

    assert acquired == Decimal(1730)
    assert disposed == Decimal(690)
    assert acquired - disposed == Decimal(1040)
