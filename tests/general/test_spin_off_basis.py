"""The cost a spin-off carries to the new holding comes from the real pool.

The first pass can only estimate the source pool, and the estimate is wrong
after any profitable sale and in any currency but GBP. Every case here uses a
90/10 split, so the new holding should receive a tenth of whatever the source
pool really cost, with the expected figures worked out by hand.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
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

    assert calculator.portfolio["FOO"].amount == Decimal(200)
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
