"""The cost a spin-off carries to the new holding comes from the real pool.

The first pass can only estimate the source pool, and the estimate is wrong
after any profitable sale and in any currency but GBP. Every case here uses a
90/10 split, so the new holding should receive a tenth of whatever the source
pool really cost, with the expected figures worked out by hand.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CapitalGainsReport
from cgt_calc.parsers.broker_registry import _transaction_sort_key
from cgt_calc.spin_off_handler import SpinOffHandler

from .calc_test_data import transaction, transfer_to_spouse_transaction
from .test_calc import get_report

BUY_DAY = datetime.date(2024, 6, 1)
SALE_DAY = datetime.date(2024, 6, 10)
SPIN_OFF_DAY = datetime.date(2024, 7, 5)
LATER = datetime.date(2024, 9, 1)

# FOO at £90 and BAR at £10 on the day, with as many BAR as FOO units, puts a
# tenth of the market value in BAR, so a tenth of the cost goes with it.
PRICES = {"FOO": {SPIN_OFF_DAY: Decimal(90)}, "BAR": {SPIN_OFF_DAY: Decimal(10)}}
FLAT = Decimal(1)


def spin_off(
    transactions: list[BrokerTransaction],
    foo_units: int,
    rates: dict[datetime.date, dict[str, Decimal]],
    currency: str = "GBP",
) -> tuple[CapitalGainsCalculator, CapitalGainsReport]:
    """Run `transactions` then spin `foo_units` of BAR out of FOO."""
    converter = CurrencyConverter(None, rates)
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, PRICES),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    # Date order, as the CLI reads them: the first pass works out the split
    # from the units held on the day, so a later sale must not come first.
    report = get_report(
        calculator,
        sorted(
            [
                *transactions,
                transaction(
                    SPIN_OFF_DAY,
                    ActionType.SPIN_OFF,
                    "BAR",
                    foo_units,
                    10,
                    0,
                    currency=currency,
                ),
            ],
            key=_transaction_sort_key,
        ),
    )
    return calculator, report


def test_baseline_without_a_prior_sale() -> None:
    """Sanity check of the harness: 10 units costing £100, a tenth goes to BAR."""
    calculator, _ = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP")],
        10,
        {BUY_DAY: {"GBP": FLAT}, SPIN_OFF_DAY: {"GBP": FLAT}},
    )

    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_after_a_profitable_sale() -> None:
    """Half sold at a profit leaves 5 units costing £50, so BAR gets £5.

    The first-pass pool had the £100 of proceeds taken off it instead of the
    £50 of cost, leaving nothing for BAR to take a share of.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, "GBP"),
        ],
        5,
        {BUY_DAY: {"GBP": FLAT}, SALE_DAY: {"GBP": FLAT}, SPIN_OFF_DAY: {"GBP": FLAT}},
    )

    assert calculator.portfolio["BAR"].amount == Decimal(5)


def test_cost_is_not_revalued_at_the_spin_off_days_rate() -> None:
    """Cost is what the shares cost in sterling when bought, not at today's rate.

    Rates are quoted as dollars per pound. $100 at $0.50/£ cost £200, so a
    tenth of that, £20, goes to BAR. The first pass kept the $100 and turned it
    into pounds at the spin-off day's $1.00/£, handing BAR £10.
    """
    calculator, _ = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "USD")],
        10,
        {BUY_DAY: {"USD": Decimal("0.5")}, SPIN_OFF_DAY: {"USD": Decimal("1.0")}},
        currency="USD",
    )

    assert calculator.portfolio["FOO"].amount == Decimal(180)
    assert calculator.portfolio["BAR"].amount == Decimal(20)


def test_after_a_transfer_to_spouse_matched_on_its_day() -> None:
    """A same-day purchase matched to a transfer leaves the pool at 10 @ £100.

    The first pass booked the transfer at the pool average instead, so its
    estimate of the pool was £133.33 and BAR was handed a third more cost than
    it should have been.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SALE_DAY, ActionType.BUY, "FOO", 5, 20, 0, -100, "GBP"),
            transfer_to_spouse_transaction(SALE_DAY, "FOO", 5),
        ],
        10,
        {BUY_DAY: {"GBP": FLAT}, SALE_DAY: {"GBP": FLAT}, SPIN_OFF_DAY: {"GBP": FLAT}},
    )

    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_cost_is_conserved_across_both_holdings() -> None:
    """What FOO gives up is exactly what BAR receives.

    Selling FOO afterwards applies its side of the split: £45 of allowable
    cost on the sale, and the £5 that went to BAR, add back up to the £50 the
    pool held before the spin-off.
    """
    calculator, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, "GBP"),
            transaction(LATER, ActionType.SELL, "FOO", 5, 30, 0, 150, "GBP"),
        ],
        5,
        {
            BUY_DAY: {"GBP": FLAT},
            SALE_DAY: {"GBP": FLAT},
            SPIN_OFF_DAY: {"GBP": FLAT},
            LATER: {"GBP": FLAT},
        },
    )

    later_sale = report.calculation_log[LATER]["sell$FOO"]
    foo_cost_given_up = sum((e.allowable_cost for e in later_sale), Decimal(0))
    assert foo_cost_given_up == Decimal(45)
    assert calculator.portfolio["BAR"].amount == Decimal(5)
    assert foo_cost_given_up + calculator.portfolio["BAR"].amount == Decimal(50)


def test_source_gives_up_its_share_on_the_day() -> None:
    """The original cost is apportioned at the reorganisation (CG51976).

    Applying it only when FOO is next sold left a holding that was never sold
    again at its full cost, and one bought into before the sale handing over
    a share of shares that had nothing to do with the spin-off.
    """
    calculator, _ = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP")],
        10,
        {BUY_DAY: {"GBP": FLAT}, SPIN_OFF_DAY: {"GBP": FLAT}},
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(10)


def test_a_later_purchase_of_the_source_is_untouched() -> None:
    """£200 of FOO bought after the spin-off keeps all £200 of its cost."""
    _, report = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(LATER, ActionType.BUY, "FOO", 20, 10, 0, -200, "GBP"),
            transaction(
                datetime.date(2024, 10, 1),
                ActionType.SELL,
                "FOO",
                30,
                20,
                0,
                600,
                "GBP",
            ),
        ],
        10,
        {
            BUY_DAY: {"GBP": FLAT},
            SPIN_OFF_DAY: {"GBP": FLAT},
            LATER: {"GBP": FLAT},
            datetime.date(2024, 10, 1): {"GBP": FLAT},
        },
    )

    # £90 left from the spin-off plus £200 bought later, not 90% of £300.
    assert report.allowable_costs == Decimal(290)


def test_a_purchase_of_the_new_holding_on_the_day_keeps_its_cost() -> None:
    """Only the spin-off's share of the day's acquisitions is replaced.

    The day's acquisitions of BAR are one entry, so overwriting it with the
    spin-off's figure threw away the £50 paid for five more.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 5, 10, 0, -50, "GBP"),
        ],
        10,
        {BUY_DAY: {"GBP": FLAT}, SPIN_OFF_DAY: {"GBP": FLAT}},
    )

    assert calculator.portfolio["BAR"].quantity == Decimal(15)
    assert calculator.portfolio["BAR"].amount == Decimal(60)


def test_selling_the_new_holding_just_before_the_spin_off_is_refused() -> None:
    """A sale the B&B rule would match to unpriced spin-off shares is refused.

    BAR was already held and sold ten days before more arrived by spin-off.
    Their cost is a share of FOO's pool on the spin-off day, which the walk
    has not reached, and the first-pass figure is £0 here because FOO was
    sold at a profit first. There is no right number to use, so say so.
    """
    with pytest.raises(CalculationError, match="run again with this one disposal"):
        spin_off(
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(BUY_DAY, ActionType.BUY, "BAR", 2, 1, 0, -2, "GBP"),
                transaction(SALE_DAY, ActionType.SELL, "FOO", 5, 20, 0, 100, "GBP"),
                transaction(
                    datetime.date(2024, 6, 25),
                    ActionType.SELL,
                    "BAR",
                    2,
                    3,
                    0,
                    6,
                    "GBP",
                ),
            ],
            5,
            {
                BUY_DAY: {"GBP": FLAT},
                SALE_DAY: {"GBP": FLAT},
                datetime.date(2024, 6, 25): {"GBP": FLAT},
                SPIN_OFF_DAY: {"GBP": FLAT},
            },
        )


def test_a_spin_off_before_the_period_is_applied_but_not_reported() -> None:
    """Like a purchase before the period: it shapes the pool, not the report.

    It used to appear in the report under its own, earlier date whenever a
    disposal inside the period happened to be the one that triggered it.
    """
    earlier_buy = datetime.date(2023, 6, 1)
    earlier_spin_off = datetime.date(2023, 7, 5)
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {earlier_spin_off: Decimal(90)},
                "BAR": {earlier_spin_off: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    report = get_report(
        calculator,
        [
            transaction(earlier_buy, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(
                earlier_spin_off, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency="GBP"
            ),
            transaction(LATER, ActionType.SELL, "FOO", 10, 20, 0, 200, "GBP"),
        ],
    )

    # The 2024/25 sale gets the apportioned £90, and nothing from 2023 is listed.
    assert report.allowable_costs == Decimal(90)
    assert earlier_spin_off not in report.calculation_log


def test_the_days_purchases_of_the_source_are_in_the_pool_whatever_the_order() -> None:
    """Same-day acquisitions are one acquisition (s105), so they share the cost.

    BAR is listed before FOO, so it used to be pooled first and the spin-off
    read FOO's pool without the day's £100 purchase: FOO £190 and BAR £60
    instead of £180 and £70.
    """
    calculator, _ = spin_off(
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAR", 5, 10, 0, -50, "GBP"),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
        ],
        20,
        {BUY_DAY: {"GBP": FLAT}, SPIN_OFF_DAY: {"GBP": FLAT}},
    )

    assert calculator.portfolio["FOO"].amount == Decimal(180)
    assert calculator.portfolio["BAR"].amount == Decimal(70)


def test_two_spin_offs_from_one_source_on_one_day_are_both_recorded() -> None:
    """Each spin-off gets its own entry, and each takes its share in turn."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
                "BAZ": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency="GBP"
            ),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency="GBP"
            ),
        ],
    )

    entries = report.calculation_log[SPIN_OFF_DAY]["spin-off$FOO"]
    # £100 -> £90 after BAR took a tenth, then £90 -> £81 after BAZ took a tenth.
    assert [e.new_pool_cost for e in entries] == [Decimal(90), Decimal(81)]
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert calculator.portfolio["BAZ"].amount == Decimal(9)
    assert calculator.portfolio["FOO"].amount == Decimal(81)


def _chain_calculator() -> CapitalGainsCalculator:
    """FOO spins BAR off, and BAR spins BAZ off, on the same day."""
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "BAR"}
    return CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
                "BAZ": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )


def test_a_chain_of_spin_offs_on_one_day_is_applied_in_order() -> None:
    """BAZ must not be apportioned until BAR has received its shares from FOO.

    BAZ is bought before either spin-off, so it used to be processed first,
    while BAR was still empty, and took a share of nothing. FOO gives BAR a
    tenth of £100; BAR, worth the same as the BAZ it spins off, then gives
    BAZ half of that £10.
    """
    calculator = _chain_calculator()
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAZ", 5, 10, 0, -50, "GBP"),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency="GBP"
            ),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency="GBP"
            ),
        ],
    )

    assert calculator.portfolio["FOO"].amount == Decimal(90)
    assert calculator.portfolio["BAR"].amount == Decimal(5)
    assert calculator.portfolio["BAZ"].amount == Decimal(55)


def test_a_chain_listed_out_of_order_is_refused() -> None:
    """BAR spinning BAZ off before FOO has spun BAR off cannot be worked out.

    The first pass goes by the order the input lists same-day rows in, and
    here it would apportion a BAR that has no shares yet.
    """
    with pytest.raises(CalculationError, match="List the spin-off that creates BAR"):
        get_report(
            _chain_calculator(),
            [
                transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency="GBP"
                ),
                transaction(
                    SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency="GBP"
                ),
            ],
        )


def test_sibling_spin_offs_are_applied_in_event_order() -> None:
    """Two spin-offs from FOO take their shares in the order they happened.

    BAR's comes first, so BAR takes a tenth of £100 and BAZ a tenth of the
    £90 left. BAZ was also bought earlier in the day, which put it first in
    the day's acquisitions, and it used to take its share first: £9 to BAR
    and £10 to BAZ, the right total in the wrong holdings.
    """
    converter = CurrencyConverter(None, {})
    handler = SpinOffHandler()
    handler.cache = {"BAR": "FOO", "BAZ": "FOO"}
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(
            converter,
            {},
            {
                "FOO": {SPIN_OFF_DAY: Decimal(90)},
                "BAR": {SPIN_OFF_DAY: Decimal(10)},
                "BAZ": {SPIN_OFF_DAY: Decimal(10)},
            },
        ),
        handler,
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, "GBP"),
            transaction(SPIN_OFF_DAY, ActionType.BUY, "BAZ", 5, 10, 0, -50, "GBP"),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAR", 10, 10, 0, currency="GBP"
            ),
            transaction(
                SPIN_OFF_DAY, ActionType.SPIN_OFF, "BAZ", 10, 10, 0, currency="GBP"
            ),
        ],
    )

    assert calculator.portfolio["FOO"].amount == Decimal(81)
    assert calculator.portfolio["BAR"].amount == Decimal(10)
    assert calculator.portfolio["BAZ"].amount == Decimal(59)
