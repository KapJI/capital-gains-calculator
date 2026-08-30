#!/usr/bin/env python3
"""Capital Gain Calculator main module."""

from __future__ import annotations

from collections import Counter, defaultdict
import datetime
import decimal
from decimal import Decimal
from fractions import Fraction
import logging
import sys
from typing import TYPE_CHECKING, Literal

from colorama import Fore, Style

from . import render_latex
from .args_parser import (
    create_parser,
    reject_duplicate_stdin,
    reject_schwab_file_and_dir,
    resolve_reporting_period,
)
from .const import (
    BALANCE_CHECK_CONTEXT_ROWS,
    BED_AND_BREAKFAST_DAYS,
    CAPITAL_GAIN_ALLOWANCES,
    DIVIDEND_ALLOWANCES,
    DIVIDEND_CURRENCY_TO_COUNTRY,
    DIVIDEND_DOUBLE_TAXATION_RULES,
    DIVIDEND_TAX_LEAD_DAYS,
    DIVIDEND_TAX_MATCH_DAYS,
    ERI_TAX_DATE_DELTA,
    INTERNAL_START_DATE,
    ISIN_COUNTRY_CODE_LENGTH,
    MAX_CONTENDED_DATES_SHOWN,
    RENAME_DESCRIPTION_PREFIX,
    UK_CURRENCY,
)
from .currency_converter import CurrencyConverter
from .current_price_fetcher import CurrentPriceFetcher
from .dates import get_tax_year_end, get_tax_year_start, is_date
from .exceptions import (
    AmountMissingError,
    CalculatedAmountDiscrepancyError,
    CalculationError,
    CgtError,
    InvalidTransactionError,
    IsinMissingError,
    PriceMissingError,
    QuantityMissingError,
    QuantityNotPositiveError,
    SymbolMissingError,
    UnclassifiedGiftError,
)
from .initial_prices import InitialPrices
from .isin_converter import IsinConverter
from .logging import bullet, setup_logging, style_text
from .model import (
    ActionType,
    BrokerTransaction,
    CalculationEntry,
    CalculationLog,
    CalculationType,
    CapitalGainsReport,
    CurrencyCode,
    Dividend,
    DividendTaxAttribution,
    ExcessReportedIncome,
    ExcessReportedIncomeDistribution,
    ExcessReportedIncomeDistributionLog,
    ExcessReportedIncomeLog,
    ForeignAmountLog,
    ForeignCurrencyAmount,
    HmrcTransactionData,
    HmrcTransactionLog,
    PortfolioEntry,
    Position,
    RuleType,
    SpinOff,
)
from .parsers.broker_registry import BrokerRegistry
from .parsers.raw import RawTransaction
from .spin_off_handler import SpinOffHandler
from .stock_splits import (
    SplitMode,
    SplitTransformation,
    StockSplitDetail,
    StockSplitEvent,
    StockSplitTransaction,
    UnresolvedRatio,
    quantity_sign,
    scale_quantity,
    unscale_quantity,
)
from .transaction_log import add_to_list, has_key
from .util import approx_equal, normalize_amount, round_decimal, strip_zeros

if TYPE_CHECKING:
    import argparse

    from .model import TransactionSource

LOGGER = logging.getLogger(__name__)


def source_account(transaction: BrokerTransaction) -> str:
    """Return the declared account boundary this transaction was loaded under.

    Every parser records one, so a transaction without provenance is
    hand-built. Those fall back to the broker name, which groups them, and
    the safe reading of that is a boundary shared by everything from that
    broker rather than a boundary of its own.
    """
    if transaction.source is not None and transaction.source.account is not None:
        return transaction.source.account
    return f"{transaction.broker} (source unrecorded)"


def relative_to_split(
    instants: tuple[datetime.datetime, ...],
    split_source: TransactionSource | None,
    other_source: TransactionSource | None,
) -> Literal["before", "after"] | None:
    """Say whether a row happened before or after a reorganisation.

    An exported instant settles it wherever both rows carry one. Failing
    that, the only other evidence is a source that documents its same-date
    rows as being in the order they happened, which a hand-written RAW
    history does and a broker export does not. Nothing else will do: several
    parsers reorder their rows for the calculation's sake, and the registry's
    stable ordering and its action priorities for gifts, spouse transfers and
    vests are calculation plumbing rather than evidence of corporate-action
    chronology. Returns None when it cannot be told.
    """
    if instants and other_source is not None and other_source.timestamp is not None:
        if other_source.timestamp < min(instants):
            return "before"
        if other_source.timestamp > max(instants):
            return "after"
        # At or between the two halves of the reorganisation, where neither
        # unit system can be proved.
        return None
    if (
        split_source is not None
        and other_source is not None
        and split_source.rows_in_time_order
        and other_source.rows_in_time_order
        and split_source.account is not None
        and split_source.index is not None
        and other_source.index is not None
        and (split_source.parser, split_source.account, split_source.file)
        == (other_source.parser, other_source.account, other_source.file)
    ):
        return "before" if other_source.index < split_source.index else "after"
    return None


def get_amount_or_fail(transaction: BrokerTransaction) -> Decimal:
    """Return the transaction amount or raise an error if missing."""
    amount = transaction.amount
    if amount is None:
        raise AmountMissingError(transaction)
    return amount


def get_symbol_or_fail(transaction: BrokerTransaction) -> str:
    """Return the transaction symbol or raise an error if missing."""
    symbol = transaction.symbol
    if symbol is None:
        raise SymbolMissingError(transaction)
    return symbol


def get_quantity_or_fail(transaction: BrokerTransaction) -> Decimal:
    """Return the transaction quantity or raise an error if missing."""
    quantity = transaction.quantity
    if quantity is None:
        raise QuantityMissingError(transaction)
    return quantity


# Amount difference can be caused by rounding errors in the price.
# Schwab rounds down the price to 4 decimal places
#  so that the error in amount can be more than $0.01.
# For example:
# 500 shares of FOO sold at $100.00016 with $1.23 fees.
# "01/01/2024,"Sell","FOO","FOO","500","$100.0001","$1.23","$49,998.85"
# calculated_amount = 500 * 100.0001 - 1.23 = 49998.82
# amount_on_record = 49998.85 vs calculated_amount = 49998.82
def _approx_equal_price_rounding(
    amount_on_record: Decimal,
    quantity_on_record: Decimal,
    price_on_record: Decimal,
    fees_on_record: Decimal,
    calculation_type: CalculationType,
) -> bool:
    calculated_amount = Decimal(0)
    calculated_price = Decimal(0)
    if calculation_type is CalculationType.ACQUISITION:
        calculated_amount = Decimal(-1) * (
            quantity_on_record * price_on_record + fees_on_record
        )
        calculated_price = (
            Decimal(-1) * amount_on_record - fees_on_record
        ) / quantity_on_record
    elif calculation_type is CalculationType.DISPOSAL:
        calculated_amount = quantity_on_record * price_on_record - fees_on_record
        calculated_price = (amount_on_record + fees_on_record) / quantity_on_record
    in_acceptable_range = abs(calculated_price - price_on_record) < Decimal("0.0001")
    LOGGER.debug(
        "Price calculated_price %.6f vs price_on_record %s in %s",
        calculated_price,
        price_on_record,
        "acceptable range" if in_acceptable_range else "error",
    )
    if in_acceptable_range:
        return True
    accptable_amount = approx_equal(amount_on_record, calculated_amount)
    LOGGER.debug(
        "Amount amount_on_record %.6f vs calculated_amount %s in %s",
        amount_on_record,
        calculated_amount,
        "acceptable range" if accptable_amount else "error",
    )
    return accptable_amount


def _match_gifts_to_rows(
    gifts: list[tuple[int, list[Decimal]]],
    available: Counter[Decimal],
    position: int = 0,
) -> bool:
    """Give every gift one of its readings without overdrawing any row count.

    First come, first served strands a gift whose only remaining reading
    another gift took, when the other could have read differently and both
    would have fitted. So try the alternatives: depth-first over the gifts,
    at most two readings each, undoing a choice that leads nowhere. Returns
    whether every gift found a row; on failure `available` is left as it was.
    """
    if position == len(gifts):
        return True
    _, readings = gifts[position]
    for reading in readings:
        if available[reading] > 0:
            available[reading] -= 1
            if _match_gifts_to_rows(gifts, available, position + 1):
                return True
            available[reading] += 1
    return False


class CapitalGainsCalculator:
    """Main calculator class."""

    def __init__(
        self,
        tax_year: int,
        currency_converter: CurrencyConverter,
        isin_converter: IsinConverter,
        price_fetcher: CurrentPriceFetcher,
        spin_off_handler: SpinOffHandler,
        initial_prices: InitialPrices,
        interest_fund_tickers: list[str],
        *,
        cgt_exempt_tickers: list[str] | None = None,
        balance_check: bool = True,
        calc_unrealized_gains: bool = False,
        period_start: datetime.date | None = None,
        period_end: datetime.date | None = None,
    ):
        """Create calculator object.

        period_start/period_end narrow the reporting window within the
        tax year, e.g. for the HMRC 2024/25 CGT adjustment calculation.
        """
        self.tax_year = tax_year
        self.period_start = period_start
        self.period_end = period_end

        self.tax_year_start_date = period_start or get_tax_year_start(tax_year)
        self.tax_year_end_date = period_end or get_tax_year_end(tax_year)

        self.currency_converter = currency_converter
        self.isin_converter = isin_converter
        self.price_fetcher = price_fetcher
        self.spin_off_handler = spin_off_handler
        self.initial_prices = initial_prices
        self.balance_check = balance_check
        self.calc_unrealized_gains = calc_unrealized_gains
        self.interest_fund_tickers = interest_fund_tickers
        self.cgt_exempt_tickers = frozenset(
            ticker.upper() for ticker in cgt_exempt_tickers or []
        )
        self.total_uk_interest = Decimal(0)
        self.total_foreign_interest = Decimal(0)
        self.total_interest_tax = Decimal(0)

        self.acquisition_list: HmrcTransactionLog = {}
        self.disposal_list: HmrcTransactionLog = {}
        # No gain/no loss transfers to a spouse/civil partner. Kept separate from
        # disposal_list so they never enter the taxable disposal totals.
        self.transfer_to_spouse_list: HmrcTransactionLog = {}
        self.bnb_list: HmrcTransactionLog = {}
        # What each share reorganisation does to a holding, by date and
        # symbol. Decided once from the chronological source view and
        # replayed by both passes: deciding again in the second pass is how
        # one pass ends up substituting the broker's count while the other
        # scales by the ratio.
        self.splits: dict[datetime.date, dict[str, SplitTransformation]] = defaultdict(
            dict
        )
        # What a decrease may still take, on a day a reorganisation restated
        # a holding. The recorded replay puts the day's increases and its
        # reconciliation before its decreases, and this pass meets them
        # interleaved, so its running count dips below what the day really
        # holds on one side or the other of the correction.
        self.split_day_capacity: dict[tuple[str, datetime.date], Decimal] = {}
        # Which declared account boundaries have put units into each holding.
        # A broker's own single-row split states a change to its own account,
        # so it may only be read as a change to the whole pool when the whole
        # pool came from that one source. Only rows that add units count: a
        # sale or a hand-written gift takes units out of the pool and supplies
        # none, and the sources are forgotten again once a day closes with
        # the holding empty, so that a ticker traded at one broker years ago
        # cannot veto a reorganisation of a pool rebuilt entirely at another.
        self.holding_sources: dict[str, set[str]] = defaultdict(set)
        # Stores old->new mapping when a symbol changes its name.
        self.rename_list: dict[datetime.date, dict[str, str]] = defaultdict(dict)

        self.dividend_list: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        # Withholding is matched to a dividend of the same broker as well as
        # the same symbol, so both are keyed by broker. The dividends stay
        # merged across brokers in dividend_list, which is what the report
        # rows are built from.
        self.dividend_tax_list: dict[
            tuple[str, str, datetime.date], ForeignCurrencyAmount
        ] = defaultdict(ForeignCurrencyAmount)
        self.dividend_dates: dict[tuple[str, str], set[datetime.date]] = defaultdict(
            set
        )
        self._attributed_dividend_tax: DividendTaxAttribution | None = None
        self.interest_list: dict[
            tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount
        ] = defaultdict(ForeignCurrencyAmount)
        self.interest_tax_list: dict[
            tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount
        ] = defaultdict(ForeignCurrencyAmount)

        # Log for the report section related only to interests and dividends
        self.calculation_log_yields: CalculationLog = defaultdict(dict)

        self.portfolio: dict[str, Position] = defaultdict(Position)
        self.spin_offs: dict[datetime.date, list[SpinOff]] = defaultdict(list)
        # What the first pass recorded for each spun-off holding, by date and
        # symbol. It is only an estimate, and the second pass swaps exactly
        # this much out of the day's acquisitions for the real figure.
        self.spin_off_estimates: dict[tuple[datetime.date, str], Decimal] = defaultdict(
            Decimal
        )
        self.calculated = False
        # Disposals that are gifts at market value rather than sales, and
        # whether the recipient is a connected person. A loss on a gift to a
        # connected person is clogged (TCGA 1992 s18(3)) and reported on its own.
        self.gift_disposals: dict[tuple[datetime.date, str], bool] = {}
        # Days on which a symbol was sold, redeemed or cashed out.
        self.sale_days: set[tuple[datetime.date, str]] = set()
        # The value per unit each day's gifts to a connected person stated,
        # to refuse a second value: one holding has one market value on a day.
        self.gift_prices: dict[
            tuple[datetime.date, str], tuple[Decimal, CurrencyCode]
        ] = {}
        # Days on which a symbol was charged a management fee. A fee adds to
        # the pool's cost with no shares, so it cannot be told from the pool
        # itself once it is in.
        self.fee_days: set[tuple[datetime.date, str]] = set()
        # The source side of each spin-off, recorded when it is applied and
        # collected into the calculation log by date.
        self.spin_off_entries: dict[
            datetime.date, dict[str, list[CalculationEntry]]
        ] = defaultdict(lambda: defaultdict(list))
        self.eris: ExcessReportedIncomeLog = defaultdict(dict)
        self.eris_distribution: ExcessReportedIncomeDistributionLog = defaultdict(
            lambda: defaultdict(ExcessReportedIncomeDistribution)
        )

    def date_in_tax_year(self, date: datetime.date) -> bool:
        """Check if date is within current tax year."""
        assert is_date(date)
        return self.tax_year_start_date <= date <= self.tax_year_end_date

    def is_cgt_exempt(self, symbol: str) -> bool:
        """Check if symbol is exempt from Capital Gains Tax."""
        return symbol.upper() in self.cgt_exempt_tickers

    def get_eri(self, symbol: str, date: datetime.date) -> ExcessReportedIncome | None:
        """Return Excess Reported Income at specific date for the input symbol."""
        return self.eris.get(date, {}).get(symbol)

    def add_acquisition(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Record an acquisition in the portfolio and the acquisition list.

        Every figure in money comes from the row's own quantity and price. A
        row that a later reorganisation on the same day has restated pools a
        different number of units for the same money, so only the count
        changes.
        """
        symbol = get_symbol_or_fail(transaction)
        quantity = transaction.quantity
        price = transaction.price

        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)

        # Add to acquisition_list to apply same day rule
        if transaction.action is ActionType.STOCK_ACTIVITY:
            if price is None:
                price = self.initial_prices.get(transaction.date, symbol)
            amount = round_decimal(quantity * price, 2)
        elif transaction.action is ActionType.TRANSFER_FROM_SPOUSE:
            # Shares received from a spouse arrive at the base cost they left
            # with (TCGA 1992 s58, CG22200), which the row states as the price.
            # No money moves, so it is pooled the way a vest is.
            if price is None:
                raise PriceMissingError(transaction)
            amount = round_decimal(quantity * price, 2)
        elif transaction.action is ActionType.SPIN_OFF:
            price, amount = self.handle_spin_off(transaction)
        else:
            if price is None:
                raise PriceMissingError(transaction)

            amount = get_amount_or_fail(transaction)
            calculated_amount = quantity * price + transaction.fees
            if not _approx_equal_price_rounding(
                amount,
                quantity,
                price,
                transaction.fees,
                CalculationType.ACQUISITION,
            ):
                raise CalculatedAmountDiscrepancyError(transaction, -calculated_amount)
            amount = -amount

        quantity = self._pooled_quantity(transaction)
        self.portfolio[symbol] += Position(quantity, amount)

        gbp_amount = self.currency_converter.to_gbp_for(amount, transaction)
        if transaction.action is ActionType.SPIN_OFF:
            self.spin_off_estimates[transaction.date, symbol] += gbp_amount
        add_to_list(
            self.acquisition_list,
            transaction.date,
            symbol,
            quantity,
            gbp_amount,
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )

    def _available_units(
        self, symbol: str, date_index: datetime.date
    ) -> Decimal | None:
        """Units a decrease may take now, or None when nothing is held.

        On an ordinary day this is the running count. On the day of a share
        reorganisation it is not: the recorded replay puts the day's increases
        and its reconciliation ahead of its decreases, while the merged
        history reaches this pass interleaved. Checking a running count there
        refuses valid histories on both sides — a positive reconciliation
        drains the count before the day's last sale, a negative one holds it
        below zero until the day's purchases land — so the day's whole
        capacity answers instead, which its decreases cannot exceed because
        planning refused a day that closes below zero.
        """
        capacity = self.split_day_capacity.get((symbol, date_index))
        if capacity is not None:
            return capacity
        if symbol not in self.portfolio:
            return None
        return self.portfolio[symbol].quantity

    def _provisional_cost(self, symbol: str, quantity: Decimal) -> Decimal:
        """Cost to take out of the first-pass pool for shares leaving it.

        Only bookkeeping, so that later same-symbol rows validate; the second
        pass works the real figure out from the rebuilt pool. On a
        reorganisation day the running count can sit at or below zero here,
        and there is then nothing to apportion.
        """
        position = self.portfolio[symbol]
        if position.quantity <= 0:
            return Decimal(0)
        return normalize_amount(position.amount * quantity / position.quantity)

    def _drop_empty_holding(self, symbol: str) -> None:
        """Drop a holding whose running count has just reached zero.

        Which accounts the units came from is deliberately not forgotten
        here. The merged stream's order within a day is not chronology:
        Trading 212 sorts equal-instant sells ahead of buys, so a running
        count can touch zero on a day the holding never emptied, and
        forgetting the other accounts then lets one account's later split
        row restate a pool that still holds their units. The sources are
        cleared when a day closes with nothing held, in
        ``_open_transaction_day``.
        """
        if self.portfolio[symbol].quantity == 0:
            del self.portfolio[symbol]

    def _pooled_quantity(self, transaction: BrokerTransaction) -> Decimal:
        """Return the count to pool, in the units in force at the day's end."""
        quantity = transaction.pool_quantity
        if quantity is None:
            raise QuantityMissingError(transaction)
        return quantity

    def plan_stock_splits(
        self,
        date_index: datetime.date,
        day_transactions: list[BrokerTransaction],
    ) -> None:
        """Decide what each of the day's reorganisations does to a holding.

        Runs before any of the day's rows are processed, so the portfolio
        still holds the day-opening pool and every source row and its ordering
        are still to hand. Both passes replay what is recorded here.
        """
        splits: dict[str, list[BrokerTransaction]] = defaultdict(list)
        for transaction in day_transactions:
            if transaction.action is ActionType.STOCK_SPLIT:
                splits[get_symbol_or_fail(transaction)].append(transaction)
        for symbol, rows in sorted(splits.items()):
            for row in rows:
                self._check_reorganisation_row(row)
            row = self._sole_reorganisation(symbol, date_index, rows)
            self._plan_stock_split(date_index, symbol, row, day_transactions)

    @staticmethod
    def _check_reorganisation_row(transaction: BrokerTransaction) -> None:
        """Refuse a reorganisation row that also states money.

        A reorganisation is neither an acquisition nor a disposal, so nothing
        is paid for it and nothing is received (TCGA 1992 s127, CG51805).
        Anything in the price, fees or amount column is therefore a figure the
        calculation has no use for and would drop without saying so, and a row
        that states one is a row about something other than the reorganisation.
        """
        quantity = transaction.quantity
        for column, value in (
            ("price", transaction.price),
            ("fees", transaction.fees),
            ("amount", transaction.amount),
        ):
            if (
                column == "amount"
                and value is not None
                and not value.is_finite()
                and quantity is not None
                and not quantity.is_finite()
            ):
                # A RAW row derives its amount from the quantity, so a
                # non-finite quantity makes a non-finite amount the user never
                # wrote. The quantity check downstream names the real defect.
                continue
            if value:
                raise InvalidTransactionError(
                    transaction,
                    f"The {column} column of a share reorganisation states "
                    f"{strip_zeros(value)}. A reorganisation buys nothing and "
                    "sells nothing (TCGA 1992 s127), so there is nothing for "
                    "that figure to be, and cgt-calc will not drop it without "
                    "saying so. Leave every money column at 0 or blank. If "
                    "money really did change hands (cash in lieu of a "
                    "fractional entitlement, say), it is a part disposal under "
                    "TCGA 1992 s128 or a small distribution under s122(2), and "
                    "cgt-calc can represent neither: leave money off this row "
                    "and work the payment and the holding's later figures out "
                    "separately",
                )
        if transaction.foreign_fees:
            listed = ", ".join(
                f"{currency} {strip_zeros(value)}"
                for currency, value in sorted(transaction.foreign_fees.items())
                if value
            )
            if listed:
                raise InvalidTransactionError(
                    transaction,
                    "The fees column of a share reorganisation states "
                    f"{listed}. A reorganisation buys nothing and sells "
                    "nothing (TCGA 1992 s127), so leave all fee columns at 0",
                )

    def _sole_reorganisation(
        self,
        symbol: str,
        date_index: datetime.date,
        rows: list[BrokerTransaction],
    ) -> BrokerTransaction:
        """Return the one row the day's reorganisation of ``symbol`` is taken from.

        Two brokers reporting the same event is not two events, and nothing in
        their exports tells the two apart, so several broker rows are refused.
        A hand-written RAW row is different: it states the change to the whole
        pooled holding by definition, which is exactly what a broker's own row
        cannot state and exactly what the calculator asks for when it refuses
        one. So a single RAW row settles the day and the broker rows for the
        same event are dropped, the way a RAW row classifying a gift drops the
        broker's own row for it.
        """
        if len(rows) == 1:
            return rows[0]
        stated_in_full = [row for row in rows if isinstance(row, RawTransaction)]
        if len(stated_in_full) != 1:
            raise CalculationError(self._two_splits_message(symbol, date_index, rows))
        raw_row = stated_in_full[0]
        broker_rows = [row for row in rows if row is not raw_row]
        broker_changes = [get_quantity_or_fail(row) for row in broker_rows]
        raw_change = get_quantity_or_fail(raw_row)
        times_by_source: dict[
            tuple[str | None, str | None, object], list[datetime.datetime | None]
        ] = defaultdict(list)
        for row in broker_rows:
            source = row.source
            source_key = (
                (source.parser, source.account, source.file)
                if source is not None
                else (None, None, None)
            )
            times_by_source[source_key].append(
                source.timestamp if source is not None else None
            )
        conflicting_times = [
            times
            for times in times_by_source.values()
            if len(times) > 1 and (None in times or len(set(times)) > 1)
        ]
        if conflicting_times:
            event_times = sorted(
                "unknown" if timestamp is None else str(timestamp)
                for times in conflicting_times
                for timestamp in times
            )
            listed = "\n".join(f"  {row}" for row in rows)
            raise CalculationError(
                f"Cannot use the RAW STOCK_SPLIT row for {symbol} on "
                f"{date_index}: the same source reports reorganisations at "
                f"different or unknown times ({', '.join(event_times)}):\n"
                f"{listed}\nThese rows are not proved to report the same "
                "reorganisation. Leave the day's STOCK_SPLIT rows out and work "
                "this day out by hand (consider professional advice)."
            )
        if not raw_change.is_finite():
            return raw_row
        if any(not change.is_finite() for change in broker_changes):
            listed = "\n".join(f"  {row}" for row in rows)
            raise CalculationError(
                f"Cannot use the RAW STOCK_SPLIT row for {symbol} on "
                f"{date_index}: a broker row states a change that cannot be "
                f"part of its change of {strip_zeros(raw_change)} units:\n"
                f"{listed}\nCorrect the broker or RAW row, or work this day "
                "out by hand (consider professional advice)."
            )
        broker_change = sum(broker_changes, Decimal(0))
        if (
            not raw_change
            or any(change * raw_change <= 0 for change in broker_changes)
            or abs(broker_change) > abs(raw_change)
        ):
            listed = "\n".join(f"  {row}" for row in rows)
            raise CalculationError(
                f"Cannot use the RAW STOCK_SPLIT row for {symbol} on "
                f"{date_index}: the broker rows change their holdings by "
                f"{strip_zeros(broker_change)} units in total, which cannot "
                "be part of its change of "
                f"{strip_zeros(raw_change)} units:\n"
                f"{listed}\nThese rows are not proved to report the same "
                "reorganisation. Correct the RAW row or work this day out by "
                "hand (consider professional advice)."
            )
        LOGGER.info(
            "Using the RAW STOCK_SPLIT row for %s on %s and ignoring %d broker "
            "row(s) for the same event: a RAW row states the change to the "
            "whole holding.",
            symbol,
            date_index,
            len(rows) - 1,
        )
        return raw_row

    def _plan_stock_split(
        self,
        date_index: datetime.date,
        symbol: str,
        row: BrokerTransaction,
        day_transactions: list[BrokerTransaction],
    ) -> None:
        """Work out and record one reorganisation."""
        instants = self._split_instants(row)
        before_rows: list[BrokerTransaction] = []
        after_rows: list[BrokerTransaction] = []
        for other in day_transactions:
            if (
                other is row
                or other.symbol != symbol
                or quantity_sign(other.action) == 0
            ):
                continue
            placement = relative_to_split(instants, row.source, other.source)
            if placement is None:
                raise CalculationError(
                    self._split_chronology_message(symbol, date_index, row, other)
                )
            (before_rows if placement == "before" else after_rows).append(other)

        day_open_quantity = self.portfolio[symbol].quantity
        holding_at_event = day_open_quantity + sum(
            (
                quantity_sign(other.action) * get_quantity_or_fail(other)
                for other in before_rows
            ),
            Decimal(0),
        )
        if holding_at_event <= 0:
            raise CalculationError(
                f"Cannot apply the reorganisation of {symbol} on {date_index}: "
                f"the holding is {strip_zeros(holding_at_event)} units at that "
                "point, so there is nothing to restate. The history is missing "
                f"the {symbol} shares it acts on."
            )

        event = self._stock_split_event(
            row, symbol, date_index, holding_at_event, instants, before_rows
        )
        ratio = event.ratio
        if isinstance(ratio, UnresolvedRatio):
            if before_rows:
                raise CalculationError(
                    self._unresolved_same_day_message(event, before_rows[0])
                )
            if holding_at_event != event.before_quantity:
                raise CalculationError(self._unresolved_scaling_message(event))
            mode = SplitMode.DIRECT_SUBSTITUTION
            exact_ratio = None
            scaled_day_open_quantity = event.after_quantity
        else:
            exact_ratio = ratio
            scaled_day_open_quantity = scale_quantity(day_open_quantity, ratio)
            mode = (
                SplitMode.BROKER_EXACT
                if holding_at_event == event.before_quantity
                else SplitMode.RATIO_ONLY
            )
            # Restate the day's earlier rows into the units the day ends in,
            # exactly once and without touching their cost, proceeds or fees.
            # A buy of 10 shares for £100 followed by a 2-for-1 split that day
            # becomes an acquisition of 20 shares for £100, which is the same
            # pool by another route and needs no special case anywhere else.
            for other in before_rows:
                other.calculation_quantity = scale_quantity(
                    get_quantity_or_fail(other), exact_ratio
                )

        # What the event leaves of the holding it acts on. The day-opening
        # pool is deliberately not the test: a holding first acquired earlier
        # the same day opens at nothing and is still restated normally.
        resulting_holding = (
            event.after_quantity
            if exact_ratio is None
            else scale_quantity(holding_at_event, exact_ratio)
        )
        if resulting_holding <= 0:
            raise CalculationError(
                f"Cannot apply the reorganisation of {symbol} on {date_index}: "
                f"it leaves {strip_zeros(resulting_holding)} units of a holding "
                f"of {strip_zeros(holding_at_event)}."
            )

        post_split_delta = self._signed_quantity(after_rows)
        normalised_day_delta = post_split_delta + self._signed_quantity(before_rows)
        if mode is SplitMode.BROKER_EXACT:
            expected_day_close = event.after_quantity + post_split_delta
            reconciliation_delta = expected_day_close - (
                scaled_day_open_quantity + normalised_day_delta
            )
        else:
            # One broker's rounded count must not adjust a pool built from
            # more than one source, and a substitution has nothing to round.
            reconciliation_delta = Decimal(0)
            expected_day_close = scaled_day_open_quantity + normalised_day_delta
        if expected_day_close < 0:
            raise CalculationError(
                f"Cannot apply the reorganisation of {symbol} on {date_index}: "
                f"the day's activity would leave {strip_zeros(expected_day_close)} "
                "units."
            )

        self.splits[date_index][symbol] = SplitTransformation(
            event=event,
            mode=mode,
            ratio=exact_ratio,
            day_open_quantity=day_open_quantity,
            scaled_day_open_quantity=scaled_day_open_quantity,
            reconciliation_delta=reconciliation_delta,
            expected_day_close=expected_day_close,
        )
        # The reorganisation belongs at the start of its day: the rows before
        # it are now stated in the units it leaves behind, so the pool has to
        # be in those units before any of them is processed. The pooled cost
        # is untouched, because a reorganisation only changes how many units
        # the same money bought (TCGA 1992 s127).
        #
        # The reconciliation goes on at the same moment, which is not where
        # the recorded replay puts it. That order -- increases, reconcile,
        # decreases -- is what the second pass follows, and the second pass is
        # what works out the tax. All this pool does is refuse a history that
        # sells shares it does not have, and for that the day's closing count
        # is the honest figure to check against: a day whose reconciliation is
        # positive can otherwise drain to nothing partway through and refuse
        # its own last disposal. A negative reconciliation can leave the count
        # briefly below zero here, ahead of the day's acquisitions, which
        # nothing reads.
        position = self.portfolio[symbol]
        self.portfolio[symbol] = Position(
            scaled_day_open_quantity + reconciliation_delta, position.amount
        )
        # Everything the day can give up, in the order the replay puts it:
        # the restated opening, then every increase, then the reconciliation.
        # Decreases draw on this rather than on the running count.
        self.split_day_capacity[symbol, date_index] = (
            scaled_day_open_quantity
            + self._signed_quantity(
                [
                    other
                    for other in (*before_rows, *after_rows)
                    if quantity_sign(other.action) > 0
                ]
            )
            + reconciliation_delta
        )

    @staticmethod
    def _signed_quantity(transactions: list[BrokerTransaction]) -> Decimal:
        """Net units these rows add to a holding, in their pooled counts."""
        total = Decimal(0)
        for transaction in transactions:
            quantity = transaction.pool_quantity
            if quantity is None:
                raise QuantityMissingError(transaction)
            total += quantity_sign(transaction.action) * quantity
        return total

    @staticmethod
    def _split_instants(row: BrokerTransaction) -> tuple[datetime.datetime, ...]:
        """Instants the export stamped on the rows behind a reorganisation."""
        if isinstance(row, StockSplitTransaction):
            return row.event.instants
        if row.source is not None and row.source.timestamp is not None:
            return (row.source.timestamp,)
        return ()

    def _stock_split_event(
        self,
        row: BrokerTransaction,
        symbol: str,
        date_index: datetime.date,
        holding_at_event: Decimal,
        instants: tuple[datetime.datetime, ...],
        before_rows: list[BrokerTransaction],
    ) -> StockSplitEvent:
        """Return the reorganisation this row states.

        A paired export states both counts itself. A single row states only a
        net change, which is a change to the whole pooled holding in a RAW
        history by definition, and a change to one account's holding when a
        broker wrote it. The second may only be read as the first once the
        whole holding is known to have come from that one account: adding one
        account's delta to a pool built from several derives a ratio that was
        never the corporate one.
        """
        if isinstance(row, StockSplitTransaction):
            return row.event
        delta = get_quantity_or_fail(row)
        if not delta.is_finite():
            raise CalculationError(
                f"Cannot apply the reorganisation of {symbol} on {date_index}: "
                f"it states a change of {delta} units, which is not a number "
                "of shares."
            )
        after_quantity = holding_at_event + delta
        if after_quantity <= 0:
            raise CalculationError(
                f"Cannot apply the reorganisation of {symbol} on {date_index}: "
                f"a change of {strip_zeros(delta)} units leaves "
                f"{strip_zeros(after_quantity)} of a holding of "
                f"{strip_zeros(holding_at_event)}."
            )
        if not isinstance(row, RawTransaction):
            contributors = self.holding_sources.get(symbol, set()) | {
                source_account(other)
                for other in before_rows
                if quantity_sign(other.action) > 0
            }
            account = source_account(row)
            if contributors - {account}:
                raise CalculationError(
                    self._broker_split_delta_message(
                        symbol, date_index, account, sorted(contributors - {account})
                    )
                )
        return StockSplitEvent(
            date=date_index,
            broker=row.broker,
            symbol_before=symbol,
            symbol_after=symbol,
            isin_before=row.isin,
            isin_after=row.isin,
            before_quantity=holding_at_event,
            after_quantity=after_quantity,
            ratio=Fraction(after_quantity) / Fraction(holding_at_event),
            instants=instants,
        )

    def handle_spin_off(
        self,
        transaction: BrokerTransaction,
    ) -> tuple[Decimal, Decimal]:
        """Handle spin off transaction.

        Documentation based on the SOLV spin-off from MMM.

        # 1. Determine the Cost Basis (Acquisition Cost) of the SOLV Shares
        In the UK, the cost basis (or acquisition cost) of the new SOLV shares
        received from the spin-off needs to be determined. This is usually done
        by apportioning part of the original cost basis of the MMM shares to
        the new SOLV shares based on their market values at the time of the
        spin-off.

        ## Step-by-Step Allocation
        * Find the Market Values:

        Determine the market value of MMM shares and SOLV shares immediately
        after the spin-off.

        * Calculate the Apportionment:

        Divide the market value of the MMM shares by the total market value of
        both MMM and SOLV shares to find the percentage allocated to MMM.
        Do the same for SOLV shares to find the percentage allocated to SOLV.

        * Allocate the Original Cost Basis:

        Multiply the original cost basis of your MMM shares by the respective
        percentages to allocate the cost basis between the MMM and SOLV shares.

        ## Example Allocation
        * Original Investment:

        Assume you bought 100 shares of MMM at £100 per share, so your total
        cost basis is £10,000.

        * Market Values Post Spin-off:

        Assume the market value of MMM shares is £90 per share and SOLV shares
        is £10 per share immediately after the spin-off.
        The total market value is £90 + £10 = £100.

        * Allocation Percentages:

        Percentage allocated to MMM: 90/100 = 90%
        Percentage allocated to SOLV: 10/100 = 10%

        * Allocate Cost Basis:

        Cost basis of MMM: £10,000 * 0.90 = £9,000
        Cost basis of SOLV: £10,000 * 0.10 = £1,000

        # 2. Determine the Holding Period
        The holding period for the SOLV shares typically includes the holding
        period of the original MMM shares. This means the date you acquired the
        MMM shares will be used as the acquisition date for the SOLV shares.
        """
        symbol = get_symbol_or_fail(transaction)
        quantity = self._pooled_quantity(transaction)

        ticker = self.spin_off_handler.get_spin_off_source(
            symbol, transaction.date, self.portfolio
        )
        # Nothing to apportion from an empty holding, and no proportion of
        # value to work out either. On a day the source is itself created by
        # a spin-off, this is the first sign the rows are out of order, and
        # it comes before any price is asked for.
        if self.portfolio[ticker].quantity == 0:
            raise CalculationError(
                f"{ticker} holds no shares on {transaction.date} when {symbol} "
                f"is spun off from it. If {ticker} is itself spun off that day, "
                f"the input lists the rows out of order: list the spin-off that "
                f"creates {ticker} first. Otherwise the history is missing "
                f"{ticker}'s shares. Do not change the dates."
            )
        # If this holding has already spun something off today, that spin-off
        # was worked out on it before it received these shares, so both its
        # split of value and its estimate are wrong. The order the input
        # lists same-day rows in is all the first pass has to go on.
        already_spun_from = next(
            (
                spin_off
                for spin_off in self.spin_offs[transaction.date]
                if spin_off.source == symbol
            ),
            None,
        )
        if already_spun_from is not None:
            raise CalculationError(
                f"On {transaction.date} {symbol} spins off "
                f"{already_spun_from.dest} and is itself spun off from {ticker}, "
                f"but the input lists the {already_spun_from.dest} spin-off "
                f"first, so it was worked out on {symbol} before {symbol} "
                f"received {ticker}'s shares. List the spin-off that creates "
                f"{symbol} before the one it makes, or work this day out by "
                "hand (consider professional advice). Do not change the dates."
            )
        dst_price = self.price_fetcher.get_closing_price(symbol, transaction.date)
        src_price = self.price_fetcher.get_closing_price(ticker, transaction.date)
        dst_amount = quantity * dst_price
        src_amount = self.portfolio[ticker].quantity * src_price
        original_src_amount = self.portfolio[ticker].amount

        share_of_original_cost = src_amount / (dst_amount + src_amount)
        self.spin_offs[transaction.date].append(
            SpinOff(
                dest=symbol,
                source=ticker,
                date=transaction.date,
                quantity=quantity,
                source_price=src_price,
                dest_price=dst_price,
            )
        )

        # What the first pass records for the new holding. It comes from the
        # first-pass pool, which is only an estimate, so the second pass
        # replaces it from the real pool (see process_acquisition). The
        # proportion recorded above is what actually carries the cost across.
        amount = (1 - share_of_original_cost) * original_src_amount
        return amount / quantity, round_decimal(amount, 2)

    def add_disposal(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Record a disposal in the portfolio and the disposal list.

        The proceeds are the row's own, worked out from the count and price it
        states. Only the count leaving the pool follows a same-day
        reorganisation.
        """
        symbol = get_symbol_or_fail(transaction)
        quantity = transaction.quantity
        available = self._available_units(symbol, transaction.date)
        if available is None:
            raise InvalidTransactionError(
                transaction, "Tried to sell not owned symbol, reversed order?"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        pooled_quantity = self._pooled_quantity(transaction)
        if available < pooled_quantity:
            raise InvalidTransactionError(
                transaction,
                f"Tried to sell more than the available balance({available})",
            )

        amount = get_amount_or_fail(transaction)
        price = transaction.price

        self.portfolio[symbol] -= Position(pooled_quantity, amount)

        self._drop_empty_holding(symbol)

        if price is None:
            raise PriceMissingError(transaction)
        # A gift to a connected person on the same day is refused (see
        # add_gift); a gift to anyone else folds into this ordinary disposal.
        if self.gift_disposals.get((transaction.date, symbol)):
            raise CalculationError(
                self._sale_and_gift_message(symbol, transaction.date)
            )
        calculated_amount = quantity * price - transaction.fees
        if not _approx_equal_price_rounding(
            amount,
            quantity,
            price,
            transaction.fees,
            CalculationType.DISPOSAL,
        ):
            raise CalculatedAmountDiscrepancyError(transaction, calculated_amount)
        add_to_list(
            self.disposal_list,
            transaction.date,
            symbol,
            pooled_quantity,
            self.currency_converter.to_gbp_for(amount, transaction),
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )
        self.sale_days.add((transaction.date, symbol))

    def add_transfer_to_spouse(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Record a no gain/no loss transfer of shares to a spouse/civil partner.

        The shares leave the holding at their base cost with a nil gain (TCGA
        1992 s58). Identification against the transferor's own same-day/30-day
        acquisitions and the Section 104 pool happens in the second pass, so
        only validation and first-pass bookkeeping happen here.
        """
        symbol = get_symbol_or_fail(transaction)
        quantity = transaction.quantity
        available = self._available_units(symbol, transaction.date)
        if available is None:
            raise InvalidTransactionError(
                transaction, "Tried to transfer to spouse a not owned symbol"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        quantity = self._pooled_quantity(transaction)
        if available < quantity:
            raise InvalidTransactionError(
                transaction,
                "Tried to transfer to spouse more than the available "
                f"balance({available})",
            )
        # A gift has no consideration, so whatever is in the price column
        # cannot affect the result. Say so rather than dropping it silently.
        if transaction.price:
            LOGGER.warning(
                "Ignoring the price on the transfer of %s to spouse on %s: "
                "a no gain/no loss transfer has no consideration",
                symbol,
                transaction.date,
            )
        # Fees are kept. They are an incidental cost of the disposal (TCGA 1992
        # s38(2), CG15250), so the consideration that gives neither gain nor
        # loss has to cover them, which passes them on to the recipient's base
        # cost. Reduce the first-pass holding so later same-symbol transactions
        # validate correctly; the actual pool cost is recomputed in the second
        # pass.
        self.portfolio[symbol] -= Position(
            quantity, self._provisional_cost(symbol, quantity)
        )
        self._drop_empty_holding(symbol)
        add_to_list(
            self.transfer_to_spouse_list,
            transaction.date,
            symbol,
            quantity,
            Decimal(0),
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )

    def add_gift(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Record shares given to someone other than a spouse.

        A gift is a disposal otherwise than by way of a bargain at arm's
        length, so its consideration is the market value of the shares on the
        day (TCGA 1992 s17, CG14530). GIFT says the recipient is a connected
        person, GIFT_UNCONNECTED that they are not; that only matters to a
        loss. The row states that value divided by its
        units as the price, which is the share price of the day unless the
        count has been restated for a later split. The disposal is then
        identified exactly like a sale. No money changes hands, so the balance
        is left alone.
        """
        symbol = get_symbol_or_fail(transaction)
        quantity = transaction.quantity
        available = self._available_units(symbol, transaction.date)
        if available is None:
            raise InvalidTransactionError(
                transaction, "Tried to give away a not owned symbol"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        if transaction.price is None:
            raise PriceMissingError(transaction)
        # Zero is a real market value: a holding can become worthless, and
        # giving it away disposes of it for nothing, leaving the cost as a
        # loss. Only a negative value is nonsense.
        if transaction.price < 0:
            raise InvalidTransactionError(
                transaction, "A gift cannot have a negative market value"
            )
        # The market value is what the row states, for the count it states.
        # Only the count leaving the pool follows a same-day reorganisation.
        market_value = quantity * transaction.price
        quantity = self._pooled_quantity(transaction)
        if available < quantity:
            raise InvalidTransactionError(
                transaction,
                f"Tried to give away more than the available balance({available})",
            )
        connected = transaction.action is ActionType.GIFT
        key = (transaction.date, symbol)
        # Everything disposed of on one day is one disposal (TCGA 1992
        # s105(1)). Gifts to the same kind of recipient accumulate, and a sale
        # folds into a gift to someone unconnected as one ordinary disposal. A
        # sale, or such a gift, alongside a gift to a connected person is
        # refused: a loss on the single disposal could not then be split for
        # the s18(3) restriction.
        if connected and key in self.sale_days:
            raise CalculationError(
                self._sale_and_gift_message(symbol, transaction.date)
            )
        if self.gift_disposals.get(key, connected) != connected:
            raise CalculationError(self._mixed_gifts_message(symbol, transaction.date))
        # Connected gifts must also agree on the value per unit. One holding
        # of one class has one market value on a day, so a second value means
        # separately valued gifts to different recipients (CG59562), and the
        # day's gain or loss could not be split between them for s18(3).
        # Everything at one value is treated as one gift to one person.
        if connected:
            value = (transaction.price, transaction.currency)
            if self.gift_prices.setdefault(key, value) != value:
                raise CalculationError(
                    self._gift_values_differ_message(symbol, transaction.date)
                )
        # Reduce the first-pass holding so later same-symbol transactions
        # validate; the pool cost is recomputed in the second pass.
        self.portfolio[symbol] -= Position(
            quantity, self._provisional_cost(symbol, quantity)
        )
        self._drop_empty_holding(symbol)
        # Recorded like a sale: the amount net of fees, the fees alongside, so
        # that proceeds come to the market value and the fees are allowed as
        # a cost of the disposal.
        add_to_list(
            self.disposal_list,
            transaction.date,
            symbol,
            quantity,
            self.currency_converter.to_gbp_for(
                market_value - transaction.fees, transaction
            ),
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )
        self.gift_disposals[transaction.date, symbol] = connected

    def _disposal_log_prefix(self, date_index: datetime.date, symbol: str) -> str:
        """Name the kind of disposal recorded for a symbol on a day.

        A sale with a gift to someone unconnected is one ordinary disposal,
        reported as a sale.

        A disposal of an instrument the user has classified as CGT-exempt is
        named "exempt" whichever kind it is, so a gift of one is reported as an
        exempt disposal rather than as a gift.
        """
        if self.is_cgt_exempt(symbol):
            return "exempt"
        key = (date_index, symbol)
        connected = self.gift_disposals.get(key)
        if connected:
            return "gift"
        if connected is None or key in self.sale_days:
            return "sell"
        return "gift-unconnected"

    @staticmethod
    def _gift_values_differ_message(symbol: str, date_index: datetime.date) -> str:
        return (
            f"Cannot compute gifts of {symbol} at different values per unit on "
            f"the same day ({date_index}): one holding has one market value on "
            "a day, so different values mean separately valued gifts to "
            "different recipients (CG59562), while TCGA 1992 s105(1) makes the "
            "day a single disposal whose gain or loss could not be split "
            "between them for the s18(3) restriction. Work this day out by "
            "hand (consider professional advice). Do not change the dates."
        )

    @staticmethod
    def _mixed_gifts_message(symbol: str, date_index: datetime.date) -> str:
        return (
            f"Cannot compute a gift to a connected person and a gift to someone "
            f"unconnected of {symbol} on the same day ({date_index}): TCGA 1992 "
            "s105(1) treats everything disposed of on one day as a single "
            "disposal, and a loss on it could not then be split between the two "
            "for the s18(3) restriction. Work this day out by hand (consider "
            "professional advice). Do not change the dates."
        )

    @staticmethod
    def _sale_and_gift_message(symbol: str, date_index: datetime.date) -> str:
        return (
            f"Cannot compute a sale and a gift to a connected person of {symbol} "
            f"on the same day ({date_index}): TCGA 1992 s105(1) treats everything disposed of "
            "on one day as a single disposal, and a loss on it could not then "
            "be attributed to the gift for the s18(3) restriction. Work this "
            "day out by hand (consider professional advice). Do not change the "
            "dates."
        )

    def _resolve_gifts(
        self,
        transactions: list[BrokerTransaction],
    ) -> list[BrokerTransaction]:
        """Drop broker-reported gifts the user has classified, refuse the rest.

        A gift to a spouse or civil partner is no gain/no loss; a gift to
        anyone else is a disposal at market value. Broker exports record only
        that the shares left, so the user says which it was by adding a RAW
        ``TRANSFER_TO_SPOUSE``, ``GIFT`` or ``GIFT_UNCONNECTED`` row for the
        same date, symbol and quantity. That row carries everything needed, so
        the broker's own row is then redundant and is dropped here.
        """
        if not any(
            transaction.action is ActionType.UNCLASSIFIED_GIFT
            for transaction in transactions
        ):
            return transactions
        # Counted, not a set: two gifts of the same shares on the same day need
        # two classifications, or one row would silently account for both.
        classified = Counter(
            (transaction.date, transaction.symbol, transaction.quantity)
            for transaction in transactions
            if transaction.action
            in {
                ActionType.TRANSFER_TO_SPOUSE,
                ActionType.GIFT,
                ActionType.GIFT_UNCONNECTED,
            }
        )
        # Gifts of one symbol on one day all draw on the same rows, and a gift
        # printed before a split can state either of two counts, so which row
        # each takes has to be settled for the day as a whole.
        groups: dict[
            tuple[datetime.date, str | None], list[tuple[int, list[Decimal]]]
        ] = defaultdict(list)
        for index, transaction in enumerate(transactions):
            if transaction.action is not ActionType.UNCLASSIFIED_GIFT:
                continue
            get_symbol_or_fail(transaction)
            quantity = transaction.quantity
            if quantity is None or quantity <= 0:
                raise QuantityNotPositiveError(transaction)
            readings = [quantity]
            if transaction.ambiguous_quantity is not None:
                readings.append(transaction.ambiguous_quantity)
            groups[transaction.date, transaction.symbol].append((index, readings))

        settled: set[int] = set()
        for (day, symbol), gifts in groups.items():
            available = Counter(
                {
                    reading: classified[day, symbol, reading]
                    for _, readings in gifts
                    for reading in readings
                }
            )
            # Gifts with one possible count go first. They have no choice, so
            # they prune the search, and when nothing fits they are the right
            # ones to blame.
            gifts.sort(key=lambda gift: len(gift[1]))
            if not _match_gifts_to_rows(gifts, available):
                # Walk the same order first come, first served to name the
                # gift that is left without a row.
                for index, readings in gifts:
                    taken = next((r for r in readings if available[r] > 0), None)
                    if taken is None:
                        raise UnclassifiedGiftError(transactions[index], readings)
                    available[taken] -= 1
                # That order is one the search already rejected, so it cannot
                # have placed every gift.
                raise AssertionError("every gift found a row after all")
            for index, _ in gifts:
                settled.add(index)
                LOGGER.debug(
                    "Gift of %s on %s is accounted for by a classifying row",
                    symbol,
                    day,
                )
        return [
            transaction
            for index, transaction in enumerate(transactions)
            if index not in settled
        ]

    def add_eri(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Add an Excess Reported Income to the list.

        UK has a specific tax regime which applies to UK investors in offshore
        funds.

        https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds

        Example of UK offshore funds are the most common UCITS ETFs (Vanguard,
        BlackRock, Xtrackers) that are normally located in Ireland.

        When those funds are "reporting" funds, that is, enlisted in HMRC
        official list of reporting funds:
        https://www.gov.uk/government/publications/offshore-funds-list-of-reporting-funds
        We need to declare the excess income periodically (yearly) from these
        funds.

        Excess Reported Income (ERI) is all the income reported by the fund
        not distributed to you (normally through dividends).
        Note that both Accumulating and Distributing funds can have reportable
        income and require reporting.

        The ERI is calculated based on the number shares owned at the end of
        the fund end reporting day for each fund.
        You multiply number of shares times the Reportable income amount per
        share as reported by each fund.

        Fund reports are directly provided in the fund website (i.e. BlackRock,
        Vanguard, Xtrackers, etc) on a yearly fashion.

        The ERI has two consequences:

        1) It increases your share cost basis at the time it materializes.
        2) It represents taxable income (either as dividend or interest
           depending on the fund type) at a future date, exactly 6 calendar
           months since the reporting day.

        Note that ERI also takes into account Bed and Breakfast so you're due
        ERI even if you sell before the reporting day and buy within 30 days.
        See https://www.rawknowledge.ltd/eri-explained-four-tricky-questions-answered/

        For some calculation example beside HMRC website you can use Vanguard
        FAQ:
        https://www.vanguardinvestor.co.uk/content/dam/intl/uk-retail-direct/general/uk-reporting-fund-faq.pdf

        Note that the current implementation doesn't take into account
        equalisation strategy which is an optional fund reporting feature that
        allows for pro rata reporting when you buy the fund shares within a
        reporting period.

        """
        distribution_date = transaction.date + ERI_TAX_DATE_DELTA

        if transaction.isin is None:
            raise IsinMissingError(transaction)
        if transaction.price is None:
            raise PriceMissingError(transaction)
        if not transaction.price.is_finite() or transaction.price < 0:
            raise InvalidTransactionError(
                transaction,
                "Excess reported income price must be finite and non-negative",
            )

        if transaction.price == Decimal(0):
            return

        price = self.currency_converter.to_gbp_for(
            transaction.price,
            transaction,
        )

        symbols = self.isin_converter.get_symbols(transaction.isin)
        for symbol in symbols:
            # For some funds we don't have symbol translation
            if not symbol:
                continue

            for report_date, report_by_symbol in self.eris.items():
                if symbol in report_by_symbol and report_date == transaction.date:
                    previous_price = report_by_symbol[symbol].price
                    if approx_equal(previous_price, price, Decimal("0.0001")):
                        LOGGER.warning(
                            "Skipping duplicated ERI transaction: %s", transaction
                        )
                        return
                    raise InvalidTransactionError(
                        transaction,
                        f"A conflicting ERI report at {report_date} for "
                        f"{symbol} of £{price} has been found at "
                        f"{report_date} of £{previous_price}",
                    )

            self.eris[transaction.date][symbol] = ExcessReportedIncome(
                price=price,
                symbol=symbol,
                date=transaction.date,
                distribution_date=distribution_date,
                is_interest=symbol in self.interest_fund_tickers,
            )

    def add_rename(self, transaction: BrokerTransaction) -> None:
        """Apply a ticker rename during the first calculation pass."""
        new_symbol = get_symbol_or_fail(transaction)
        old_symbol = transaction.description[len(RENAME_DESCRIPTION_PREFIX) :]
        if (
            not transaction.description.startswith(RENAME_DESCRIPTION_PREFIX)
            or not old_symbol
        ):
            raise InvalidTransactionError(
                transaction,
                "Rename transaction does not identify the old symbol",
            )
        existing = self.rename_list[transaction.date].get(old_symbol)
        if existing is not None and existing != new_symbol:
            raise CalculationError(
                self._two_renames_message(
                    old_symbol, transaction.date, existing, new_symbol
                )
            )
        self.rename_list[transaction.date][old_symbol] = new_symbol
        position = self.portfolio.pop(old_symbol, Position())
        self.portfolio[new_symbol] += position
        # The units keep the accounts that put them there; only the name
        # changes. Nothing is forgotten here, whatever the count says. The
        # merged stream's order within a day is not chronology, so a count of
        # zero at this row is not the old name emptying, and a rename is no
        # better placed to read it than the disposal that took it there. That
        # question is settled where every earlier day has closed, in
        # ``_open_transaction_day``, which is the only place a holding's
        # accounts are dropped.
        moved = self.holding_sources.pop(old_symbol, set())
        if moved:
            self.holding_sources[new_symbol] |= moved

    def add_management_fee(self, transaction: BrokerTransaction) -> Decimal:
        """Record a fee that increases the holding's pooled cost."""
        amount = get_amount_or_fail(transaction)
        if not amount.is_finite():
            raise InvalidTransactionError(
                transaction,
                "Fee amount must be a finite number",
            )
        if amount > 0:
            raise InvalidTransactionError(
                transaction,
                "Fee amount must not be positive: a positive fee would reduce "
                "the pooled cost",
            )
        transaction.fees = -amount
        transaction.quantity = Decimal(0)
        gbp_fees = self.currency_converter.to_gbp_for(transaction.fees, transaction)
        symbol = get_symbol_or_fail(transaction)
        self.fee_days.add((transaction.date, symbol))
        add_to_list(
            self.acquisition_list,
            transaction.date,
            symbol,
            transaction.quantity,
            gbp_fees,
            gbp_fees,
        )
        return amount

    def _convert_foreign_fees(self, transaction: BrokerTransaction) -> None:
        """Fold fees paid in other currencies into the transaction's fees.

        Parsers store fees whose currency differs from the transaction
        currency in `foreign_fees` since they cannot do currency conversion
        themselves.
        """
        if not transaction.foreign_fees:
            return
        converted = Decimal(0)
        for currency, fee in transaction.foreign_fees.items():
            fee_gbp = self.currency_converter.to_gbp(fee, currency, transaction.date)
            if transaction.currency == "GBP":
                converted += fee_gbp
            else:
                converted += fee_gbp * self.currency_converter.currency_to_gbp_rate(
                    transaction.currency, transaction.date
                )
        transaction.foreign_fees = {}
        transaction.fees += converted
        # The parser derived the price without the foreign fees; re-derive it
        # so that amount, price and fees stay mutually consistent.
        if (
            transaction.action in {ActionType.BUY, ActionType.SELL}
            and transaction.quantity
            and transaction.amount is not None
        ):
            transaction.price = (
                abs(transaction.amount + transaction.fees) / transaction.quantity
            )

    @staticmethod
    def _group_by_date(
        transactions: list[BrokerTransaction],
    ) -> dict[datetime.date, list[BrokerTransaction]]:
        """Group the history by date, so a day can be planned before it runs."""
        days: dict[datetime.date, list[BrokerTransaction]] = defaultdict(list)
        for transaction in transactions:
            days[transaction.date].append(transaction)
        return days

    def _open_transaction_day(
        self,
        transaction: BrokerTransaction,
        days: dict[datetime.date, list[BrokerTransaction]],
        planned: set[datetime.date],
    ) -> None:
        """Plan the day's reorganisations, and note where units came from."""
        if transaction.date not in planned:
            planned.add(transaction.date)
            # Every earlier day has closed, so a holding absent from the
            # portfolio now is one that really emptied rather than one whose
            # running count touched zero between a same-day sale and buy. A
            # zero-quantity entry is the same thing under a key that outlived
            # its units: renaming a sold-out holding creates the new name with
            # nothing in it, and its sources must not outlive the units either.
            for symbol in [
                held
                for held in self.holding_sources
                if held not in self.portfolio or self.portfolio[held].quantity == 0
            ]:
                del self.holding_sources[symbol]
            self.plan_stock_splits(transaction.date, days[transaction.date])
        if quantity_sign(transaction.action) > 0 and transaction.symbol:
            self.holding_sources[transaction.symbol].add(source_account(transaction))

    def convert_to_hmrc_transactions(
        self,
        transactions: list[BrokerTransaction],
    ) -> None:
        """Convert broker transactions to HMRC transactions."""
        # We keep a balance per broker,currency pair
        balance: dict[tuple[str, CurrencyCode], Decimal] = defaultdict(
            lambda: Decimal(0)
        )
        dividends: dict[tuple[str, CurrencyCode], Decimal] = defaultdict(Decimal)
        dividends_tax: dict[tuple[str, CurrencyCode], Decimal] = defaultdict(Decimal)
        interests: dict[tuple[str, CurrencyCode], Decimal] = defaultdict(Decimal)
        interest_taxes: dict[tuple[str, CurrencyCode], Decimal] = defaultdict(Decimal)
        balance_history: list[Decimal] = []

        for transaction in transactions:
            self.isin_converter.add_from_transaction(transaction)

        transactions = self._resolve_gifts(transactions)
        # Reorganisations are planned a day at a time, before any of that
        # day's rows is processed: what a row's count means depends on
        # whether a reorganisation followed it that day, and the pool has to
        # be in the day's closing units before the first of them is pooled.
        days = self._group_by_date(transactions)
        planned: set[datetime.date] = set()

        for i, transaction in enumerate(transactions):
            self._open_transaction_day(transaction, days, planned)
            self._convert_foreign_fees(transaction)
            if transaction.action == ActionType.EXCESS_REPORTED_INCOME:
                self.add_eri(transaction)
                balance_history.append(
                    Decimal(0)
                )  # dummy value, this will get filtered out
                continue
            new_balance = balance[transaction.broker, transaction.currency]
            if transaction.action in {
                ActionType.BUY,
                ActionType.REINVEST_SHARES,
            }:
                new_balance += get_amount_or_fail(transaction)
                self.add_acquisition(transaction)
            elif transaction.action in {
                ActionType.SELL,
                ActionType.CASH_MERGER,
                ActionType.FULL_REDEMPTION,
            }:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                self.add_disposal(transaction)
            elif transaction.action is ActionType.FEE:
                new_balance += self.add_management_fee(transaction)
            elif transaction.action in {
                ActionType.STOCK_ACTIVITY,
                ActionType.SPIN_OFF,
                # Shares arriving from a spouse: a cost, but no cash paid.
                ActionType.TRANSFER_FROM_SPOUSE,
            }:
                self.add_acquisition(transaction)
            elif transaction.action is ActionType.STOCK_SPLIT:
                # Already applied, to the day-opening pool, by
                # plan_stock_splits. No cash moves and no shares are acquired
                # or disposed of (TCGA 1992 s127).
                pass
            elif transaction.action in {ActionType.DIVIDEND, ActionType.CAPITAL_GAIN}:
                amount = get_amount_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                self.dividend_list[symbol, transaction.date] += ForeignCurrencyAmount(
                    amount, currency
                )
                self.dividend_dates[transaction.broker, symbol].add(transaction.date)
                if self.date_in_tax_year(transaction.date):
                    dividends[symbol, currency] += amount
            elif transaction.action is ActionType.DIVIDEND_TAX:
                amount = get_amount_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                self.dividend_tax_list[
                    transaction.broker, symbol, transaction.date
                ] += ForeignCurrencyAmount(amount, currency)
            # Cash moves and nothing else: a deposit or withdrawal, a
            # correction, or an incoming wire. No shares change hands.
            elif transaction.action in {
                ActionType.ADJUSTMENT,
                ActionType.TRANSFER,
                ActionType.WIRE_FUNDS_RECEIVED,
            }:
                new_balance += get_amount_or_fail(transaction)
            elif transaction.action is ActionType.INTEREST:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                self.interest_list[
                    transaction.broker, transaction.currency, transaction.date
                ] += ForeignCurrencyAmount(amount, transaction.currency)
                if self.date_in_tax_year(transaction.date):
                    interests[transaction.broker, transaction.currency] += amount
            elif transaction.action is ActionType.INTEREST_TAX:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                self.interest_tax_list[
                    transaction.broker, transaction.currency, transaction.date
                ] += ForeignCurrencyAmount(amount, transaction.currency)
                if self.date_in_tax_year(transaction.date):
                    interest_taxes[transaction.broker, transaction.currency] += amount
            elif transaction.action is ActionType.RENAME:
                self.add_rename(transaction)
            elif transaction.action in {
                ActionType.TRANSFER_TO_SPOUSE,
                ActionType.GIFT,
                ActionType.GIFT_UNCONNECTED,
            }:
                # Shares leave for no money, so the balance is left alone. A fee
                # on the row is real money, but these rows are written by hand
                # in a RAW file, which is its own unfunded "Unknown" ledger, so
                # charging the fee there only ever drives that balance negative.
                # The fee still counts as a cost; it is the cash check it cannot
                # take part in.
                if transaction.action is ActionType.TRANSFER_TO_SPOUSE:
                    self.add_transfer_to_spouse(transaction)
                else:
                    self.add_gift(transaction)
            elif transaction.action is ActionType.REINVEST_DIVIDENDS:
                LOGGER.warning("Ignoring unsupported action: %s", transaction.action)
            else:
                raise InvalidTransactionError(
                    transaction, f"Action not processed({transaction.action})"
                )
            balance_history.append(new_balance)
            if self.balance_check and new_balance < 0:
                entries = [
                    f"{trx}\nBalance after transaction={balance_after}"
                    for trx, balance_after in zip(
                        transactions[: i + 1], balance_history, strict=True
                    )
                    # Only same-pool transactions that moved the cash balance
                    # are relevant (skips other currencies, ERI entries,
                    # renames, splits, etc.)
                    if trx.broker == transaction.broker
                    and trx.currency == transaction.currency
                    and trx.amount
                ]
                omitted = len(entries) - BALANCE_CHECK_CONTEXT_ROWS
                if omitted > 0:
                    entries = [
                        f"... {omitted} earlier transaction(s) omitted ...",
                        *entries[-BALANCE_CHECK_CONTEXT_ROWS:],
                    ]
                msg = f"Reached a negative balance({new_balance})"
                msg += f" for broker {transaction.broker} ({transaction.currency})"
                msg += " after processing the following transactions:\n"
                msg += "\n".join(entries) + "\n"
                msg += "Tip: If your input file is missing deposits/withdrawals use --no-balance-check."
                raise CalculationError(msg)
            balance[transaction.broker, transaction.currency] = new_balance

        # Withholding belongs to the year of the dividend it was taken from,
        # which is not always the year it was posted in, so the summary reads
        # it from the same attribution the report is built from. Tax that
        # matched no dividend is counted under its own date: no dividend row
        # can carry it, but the broker took it and the summary says so. The
        # warning raised for it explains why the report does not show it.
        attribution = self._dividend_tax_attribution()
        for (symbol, date), tax in [
            *attribution.matched.items(),
            *attribution.unmatched.items(),
        ]:
            if self.date_in_tax_year(date) and tax.currency is not None:
                dividends_tax[symbol, tax.currency] += tax.amount

        self.first_pass_report(
            balance,
            dividends,
            dividends_tax,
            interests,
            interest_taxes,
        )

    def first_pass_report(
        self,
        balance: dict[tuple[str, CurrencyCode], Decimal],
        dividends: dict[tuple[str, CurrencyCode], Decimal],
        dividends_tax: dict[tuple[str, CurrencyCode], Decimal],
        interests: dict[tuple[str, CurrencyCode], Decimal],
        interest_taxes: dict[tuple[str, CurrencyCode], Decimal],
    ) -> None:
        """Print the results of the first pass."""
        LOGGER.info(
            "\n%s\n",
            style_text(
                "First pass complete", colour=Fore.GREEN, emoji="✅", stream=sys.stderr
            ),
        )
        bul = bullet(sys.stdout)
        print(style_text("Final portfolio", colour=Style.BRIGHT, emoji="📊"))
        if not self.portfolio:
            print(f"{bul}(none)")
        for stock, position in sorted(self.portfolio.items()):
            print(f"{bul}{stock}: {position}")
        print()
        print(style_text("Final balance", colour=Style.BRIGHT, emoji="💰"))
        for (broker, currency), amount in balance.items():
            print(f"{bul}{broker}: {round_decimal(amount, 2)} ({currency})")
        # Withholding can arrive without income in the same run, so taxed
        # symbols are listed even when no dividend was paid. A tax-only
        # entry whose withholding rounds to 0.00, or is a net refund, has
        # nothing to display, so it is left out entirely.
        keys = list(dividends)
        keys += [key for key in dividends_tax if key not in dividends]
        dividend_lines = []
        for symbol, currency in keys:
            amount = dividends.get((symbol, currency), Decimal(0))
            tax = round_decimal(-dividends_tax.get((symbol, currency), Decimal(0)), 2)
            if (symbol, currency) not in dividends and tax <= 0:
                continue
            tax_str = f", excluding {tax} taxed at source" if tax > 0 else ""
            dividend_lines.append(
                f"{bul}{symbol}: {round_decimal(amount, 2)}{tax_str} ({currency})"
            )
        if dividend_lines:
            print()
            print(style_text("Dividends", colour=Style.BRIGHT, emoji="💵"))
            for line in dividend_lines:
                print(line)
        if interests:
            print()
            print(style_text("Interest", colour=Style.BRIGHT, emoji="🏦"))
            for (broker, currency), amount in interests.items():
                print(f"{bul}{broker}: {round_decimal(amount, 2)} ({currency})")
        if interest_taxes:
            print()
            print(style_text("Interest taxes", colour=Style.BRIGHT, emoji="🧾"))
            for (broker, currency), amount in interest_taxes.items():
                print(f"{bul}{broker}: {round_decimal(-amount, 2)} ({currency})")
        print()

    def process_acquisition(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> list[CalculationEntry]:
        """Process single acquisition."""
        acquisition = self.acquisition_list[date_index][symbol]
        spin_offs_here = [
            spin_off
            for spin_off in self.spin_offs.get(date_index, [])
            if spin_off.dest == symbol
        ]
        spin_off = spin_offs_here[0] if spin_offs_here else None
        if spin_offs_here:
            # A spin-off carries a share of the source's cost across, and the
            # original cost is apportioned between the two holdings at the
            # reorganisation itself (CG51976). The first pass could only
            # estimate the source pool, so it recorded an estimate for this
            # holding; here the pool is authoritative and in GBP. Swap exactly
            # the estimate out of the day's acquisitions, which may also hold
            # ordinary purchases of the same symbol, and take the source's
            # side at the same moment so that what one gives up is what the
            # other receives.
            carried = Decimal(0)
            # Several rows for one spin-off, as when the holding is split
            # across brokers, are one reorganisation: the split of value is by
            # the whole of what was received, and the source gives up its
            # share once. Rows from different sources stay separate events,
            # taken in the order they happened.
            events: dict[str, list[SpinOff]] = {}
            for row in spin_offs_here:
                events.setdefault(row.source, []).append(row)
            for source_symbol, rows in events.items():
                first = rows[0]
                source = self.portfolio[
                    self._spin_off_pool(date_index, source_symbol, first.dest)
                ]
                assert first.source_price is not None
                assert first.dest_price is not None
                received = sum((row.quantity for row in rows), Decimal(0))
                source_value = source.quantity * first.source_price
                total_value = source_value + received * first.dest_price
                proportion = source_value / total_value if total_value else Decimal(1)
                share = round_decimal((1 - proportion) * source.amount, 2)
                carried += share
                self.spin_off_entries[date_index][source_symbol].append(
                    CalculationEntry(
                        RuleType.SPIN_OFF,
                        quantity=source.quantity,
                        amount=-source.amount,
                        new_quantity=source.quantity,
                        gain=None,
                        # Fees, if any, are already accounted for on the
                        # acquisition of spun-off shares
                        fees=Decimal(0),
                        new_pool_cost=source.amount - share,
                        allowable_cost=source.amount - share,
                        spin_off=first,
                    )
                )
                source.amount -= share
            acquisition.amount += carried - self.spin_off_estimates.pop(
                (date_index, symbol), Decimal(0)
            )
        modified_amount = acquisition.amount
        position = self.portfolio[symbol]
        calculation_entries = []
        # Management fee transaction can have 0 quantity
        assert acquisition.quantity >= 0
        # Stock split can have 0 amount
        assert acquisition.amount >= 0

        bnb_acquisition = HmrcTransactionData()
        bed_and_breakfast_fees = Decimal(0)

        if acquisition.quantity > 0 and has_key(self.bnb_list, date_index, symbol):
            bnb_acquisition = self.bnb_list[date_index][symbol]
            assert bnb_acquisition.quantity <= acquisition.quantity
            # Multiply by the B&B quantity before dividing to avoid rounding errors from division
            bnb_cost_basis = normalize_amount(
                (bnb_acquisition.quantity * acquisition.amount) / acquisition.quantity
            )
            modified_amount -= bnb_cost_basis
            modified_amount += bnb_acquisition.amount
            assert modified_amount > 0
            bed_and_breakfast_fees = (
                acquisition.fees * bnb_acquisition.quantity / acquisition.quantity
            )
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.BED_AND_BREAKFAST,
                    quantity=bnb_acquisition.quantity,
                    amount=-bnb_acquisition.amount,
                    new_quantity=position.quantity + bnb_acquisition.quantity,
                    new_pool_cost=position.amount + bnb_acquisition.amount,
                    fees=bed_and_breakfast_fees,
                    allowable_cost=acquisition.amount,
                    eris=bnb_acquisition.eris,
                )
            )
        self.portfolio[symbol] += Position(
            acquisition.quantity,
            modified_amount,
        )
        if (
            acquisition.quantity - bnb_acquisition.quantity > 0
            or bnb_acquisition.quantity == 0
        ):
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
                    quantity=acquisition.quantity - bnb_acquisition.quantity,
                    amount=-(modified_amount - bnb_acquisition.amount),
                    new_quantity=position.quantity + acquisition.quantity,
                    new_pool_cost=position.amount + modified_amount,
                    fees=acquisition.fees - bed_and_breakfast_fees,
                    allowable_cost=acquisition.amount,
                    spin_off=spin_off,
                )
            )
        return calculation_entries

    def process_disposal(
        self,
        symbol: str,
        date_index: datetime.date,
        *,
        no_gain_no_loss: bool = False,
    ) -> tuple[Decimal, list[CalculationEntry]]:
        """Process a single disposal.

        With ``no_gain_no_loss`` the event is a transfer to a spouse/civil
        partner: it uses the same same-day/30-day/Section 104 identification as a
        disposal, but each matched tranche's deemed proceeds equal its allowable
        cost so the gain is nil (TCGA 1992 s58), and the source rows come from
        ``transfer_to_spouse_list`` (which carry no fees and no proceeds).
        """
        disposal = (
            self.transfer_to_spouse_list if no_gain_no_loss else self.disposal_list
        )[date_index][symbol]
        disposal_quantity = disposal.quantity
        proceeds_amount = disposal.amount
        original_disposal_quantity = disposal_quantity
        disposal_price = proceeds_amount / disposal_quantity
        current_quantity = self.portfolio[symbol].quantity
        current_amount = self.portfolio[symbol].amount
        assert disposal_quantity <= current_quantity
        chargeable_gain = Decimal(0)
        calculation_entries = []
        # Same day rule is first, against the day's real purchases only. Shares
        # from a split are not an acquisition (TCGA 1992 s127, CG51805) and cost
        # nothing, so matching them would hand the disposal a nil allowable cost
        # and, on a day that also has a purchase, spread that purchase's cost
        # over the free shares as well.
        same_day_acquisition = self._matchable_acquisition(date_index, symbol)
        if same_day_acquisition.quantity > 0:
            available_quantity = min(disposal_quantity, same_day_acquisition.quantity)
            if available_quantity > 0:
                fees = disposal.fees * available_quantity / original_disposal_quantity

                # Multiply by available_quantity before divide to avoid rounding errors from division
                acquisition_cost = normalize_amount(
                    (available_quantity * same_day_acquisition.amount)
                    / same_day_acquisition.quantity
                )

                acquisition_price = acquisition_cost / available_quantity
                # No gain/no loss: deemed proceeds equal the allowable cost.
                same_day_amount = (
                    acquisition_cost
                    if no_gain_no_loss
                    else available_quantity * disposal_price
                )
                same_day_proceeds = same_day_amount + fees
                same_day_allowable_cost = acquisition_cost + fees
                same_day_gain = same_day_proceeds - same_day_allowable_cost
                chargeable_gain += same_day_gain
                LOGGER.debug(
                    "SAME DAY, quantity %s, gain %s, disposal price %s, "
                    "acquisition price %s",
                    available_quantity,
                    same_day_gain,
                    disposal_price,
                    acquisition_price,
                )
                disposal_quantity -= available_quantity
                proceeds_amount -= available_quantity * disposal_price
                current_quantity -= available_quantity
                # These shares shouldn't be added to Section 104 holding
                current_amount -= acquisition_cost
                if current_quantity == 0:
                    assert round_decimal(current_amount, 23) == 0, (
                        f"current amount {current_amount}"
                    )
                calculation_entries.append(
                    CalculationEntry(
                        rule_type=(
                            RuleType.TRANSFER_TO_SPOUSE
                            if no_gain_no_loss
                            else RuleType.SAME_DAY
                        ),
                        quantity=available_quantity,
                        amount=same_day_amount,
                        gain=same_day_gain,
                        allowable_cost=same_day_allowable_cost,
                        fees=fees,
                        new_quantity=current_quantity,
                        new_pool_cost=current_amount,
                    )
                )

        # Bed and breakfast rule next
        if disposal_quantity > 0:
            eris = []
            eri = self.get_eri(symbol, date_index)
            if eri:
                eris.append(eri)

            # A reorganisation between the disposal and the repurchase
            # leaves the two counted in different units. The ratios compose
            # exactly, as one fraction, and are divided by once at the end:
            # multiplying rounded decimal multipliers day by day compounds the
            # very error the exact ratio removed.
            cumulative_ratio = Fraction(1)
            unresolved_splits: list[tuple[datetime.date, StockSplitEvent]] = []
            effective_symbol = symbol

            for i in range(BED_AND_BREAKFAST_DAYS):
                search_index = date_index + datetime.timedelta(days=i + 1)
                # HMRC treats renames as the same security for B&B purposes.
                # A reorganisation is recorded under the name the holding
                # carried when it happened, which on a day the holding is also
                # renamed is the name it entered the day under. Look there
                # first and under the day's new name second, then carry on
                # under the name the day ends with. Only one can be there:
                # a day whose rename pools two holdings is refused before
                # either pass reaches it (see apply_split_openings). A
                # reorganisation of a holding renamed *into* this name is
                # deliberately not picked up: the shares disposed of here left
                # before that one arrived, so they were never its shares.
                renames = self.rename_list.get(search_index, {})
                day_splits = self.splits.get(search_index, {})
                renamed_symbol = renames.get(effective_symbol, effective_symbol)
                transformation = day_splits.get(effective_symbol) or day_splits.get(
                    renamed_symbol
                )
                effective_symbol = renamed_symbol
                if transformation is not None:
                    if transformation.ratio is None:
                        unresolved_splits.append((search_index, transformation.event))
                    else:
                        cumulative_ratio *= transformation.ratio
                    # A reorganisation this close to a disposal is worth a
                    # second look, whether or not a match follows.
                    LOGGER.warning(
                        "A split happened shortly after a disposal of %s, double check these transactions."
                        "Disposed on %s and split happened on %s",
                        symbol,
                        date_index,
                        search_index,
                    )

                # ERI is distributed annually, but when a fund closes we might have
                # multiple ERI distributions in close succession
                eri = self.get_eri(effective_symbol, search_index)
                if eri:
                    eris.append(eri)
                acquisition = self._matchable_acquisition(
                    search_index, effective_symbol
                )
                if acquisition.quantity > 0:
                    bnb_acquisition = (
                        self.bnb_list[search_index][effective_symbol]
                        if has_key(self.bnb_list, search_index, effective_symbol)
                        else HmrcTransactionData()
                    )
                    assert bnb_acquisition.quantity <= acquisition.quantity

                    same_day_disposal = (
                        self.disposal_list[search_index][effective_symbol]
                        if has_key(self.disposal_list, search_index, effective_symbol)
                        else HmrcTransactionData()
                    )
                    # A same-day transfer to spouse competes for the same-day
                    # acquisition like a same-day sale, so reserve its share too.
                    if has_key(
                        self.transfer_to_spouse_list, search_index, effective_symbol
                    ):
                        same_day_disposal = (
                            same_day_disposal
                            + self.transfer_to_spouse_list[search_index][
                                effective_symbol
                            ]
                        )
                    if same_day_disposal.quantity > acquisition.quantity:
                        # If the number of shares disposed of exceeds the number
                        # acquired on the same day the excess shares will be identified
                        # in the normal way.
                        continue

                    # This can be some management fee entry or already used
                    # by bed and breakfast rule
                    if (
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity
                        == 0
                    ):
                        continue
                    if any(
                        spin_off.dest == effective_symbol
                        for spin_off in self.spin_offs.get(search_index, [])
                    ):
                        # What those shares cost is a share of the source's pool
                        # on the day of the spin-off, and the walk has not
                        # reached that day. The only figure to hand is the
                        # first-pass estimate, and that is wrong after any
                        # profitable sale, so refuse rather than use it.
                        raise CalculationError(
                            f"Cannot compute the disposal of {symbol} on "
                            f"{date_index}: a spin-off added {effective_symbol} "
                            f"shares on {search_index}, within the following 30 "
                            "days, and the bed and breakfast rule would identify "
                            "this disposal against them. What they cost is a "
                            "share of the source holding's pool on the day of the "
                            "spin-off, which is not settled until that day, so "
                            "this tool cannot say what they cost here.\n"
                            "What to do: run again with this one disposal left "
                            "out. That run still applies the spin-off to both "
                            "holdings and shows what the spun-off shares cost, "
                            f"as the {effective_symbol} acquisition on "
                            f"{search_index}. Identify this disposal against them "
                            "at that cost by hand (consider professional advice). "
                            f"Any later {effective_symbol} figures in that run "
                            "still include the shares disposed of here, so check "
                            "those by hand as well. Do not leave the symbol or "
                            "the spin-off row out: the spin-off also reduces the "
                            "source holding's cost."
                        )
                    # Bed and breakfasting is a record of how the disposal was
                    # matched rather than a problem. Surface it for the computed
                    # tax year; the rest of the history walk logs it at DEBUG.
                    LOGGER.log(
                        logging.INFO
                        if self.date_in_tax_year(date_index)
                        else logging.DEBUG,
                        "Bed & breakfast match: %s %s %s, re-acquired %s",
                        symbol,
                        "transferred to spouse" if no_gain_no_loss else "disposed",
                        date_index,
                        search_index,
                    )
                    # The conversion needs the ratio, so this is where an
                    # unrecoverable one is refused. Not merely for happening
                    # inside the window: an acquisition before it, or a
                    # disposal already matched, needs no conversion at all.
                    if unresolved_splits:
                        split_date, unresolved_event = unresolved_splits[0]
                        raise CalculationError(
                            self._unresolved_bnb_message(
                                unresolved_event, symbol, date_index, split_date
                            )
                        )
                    if cumulative_ratio != 1:
                        LOGGER.warning(
                            "Bed & breakfast for %s is taking into account a %sx split "
                            "that happened shortly before the repurchase of shares",
                            symbol,
                            cumulative_ratio,
                        )
                    # Everything reserved here is counted in the acquisition's
                    # own units, so the whole remainder converts to the
                    # disposal's units once. Converting only the acquisition
                    # would subtract post-split counts from a pre-split one and
                    # can go negative.
                    available_acquisition_units = (
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity
                    )
                    available_quantity, consumed_acquisition_units = (
                        self._match_across_splits(
                            disposal_quantity,
                            available_acquisition_units,
                            cumulative_ratio,
                            symbol=symbol,
                            date_index=date_index,
                            search_index=search_index,
                        )
                    )
                    fees = (
                        disposal.fees * available_quantity / original_disposal_quantity
                    )
                    # Multiply by the consumed quantity before dividing to
                    # avoid rounding errors from division. Both counts here
                    # are in the acquisition's own units.
                    bnb_acquisition_cost = normalize_amount(
                        (consumed_acquisition_units * acquisition.amount)
                        / acquisition.quantity
                    )
                    acquisition_price = bnb_acquisition_cost / available_quantity
                    # No gain/no loss: deemed proceeds equal the allowable cost.
                    bed_and_breakfast_amount = (
                        bnb_acquisition_cost
                        if no_gain_no_loss
                        else available_quantity * disposal_price
                    )
                    bed_and_breakfast_proceeds = bed_and_breakfast_amount + fees
                    bed_and_breakfast_allowable_cost = bnb_acquisition_cost + fees
                    # ERI needs to be reported when doing bed and breakfast as if you
                    # held the stocks at the reporting end date.
                    # https://www.rawknowledge.ltd/eri-explained-four-tricky-questions-answered/
                    total_dist_amount = Decimal(0)
                    for eri in eris:
                        eri_distribution = ExcessReportedIncomeDistribution(
                            price=eri.price,
                            amount=available_quantity * eri.price,
                            quantity=available_quantity,
                        )
                        total_dist_amount += eri_distribution.amount
                        if self.date_in_tax_year(eri.distribution_date):
                            self.eris_distribution[eri.distribution_date][symbol] += (
                                eri_distribution
                            )

                    bed_and_breakfast_gain = (
                        bed_and_breakfast_proceeds - bed_and_breakfast_allowable_cost
                    )
                    chargeable_gain += bed_and_breakfast_gain
                    LOGGER.debug(
                        "BED & BREAKFAST, quantity %s, gain %s, disposal price %s, "
                        "acquisition price %s%s",
                        available_quantity,
                        bed_and_breakfast_gain,
                        disposal_price,
                        acquisition_price,
                        f", added_excess_income: {total_dist_amount}"
                        if total_dist_amount > 0
                        else "",
                    )
                    disposal_quantity -= available_quantity
                    proceeds_amount -= available_quantity * disposal_price

                    # Multiply by available_quantity before divide to avoid rounding errors from division
                    amount_delta = normalize_amount(
                        (available_quantity * current_amount) / current_quantity
                    )

                    current_quantity -= available_quantity
                    current_amount -= amount_delta
                    if current_quantity == 0:
                        assert round_decimal(current_amount, 23) == 0, (
                            f"current amount {current_amount}"
                        )
                    add_to_list(
                        self.bnb_list,
                        search_index,
                        effective_symbol,
                        consumed_acquisition_units,
                        amount_delta + total_dist_amount,
                        Decimal(0),
                        eris,
                    )
                    calculation_entries.append(
                        CalculationEntry(
                            rule_type=(
                                RuleType.TRANSFER_TO_SPOUSE
                                if no_gain_no_loss
                                else RuleType.BED_AND_BREAKFAST
                            ),
                            quantity=available_quantity,
                            amount=bed_and_breakfast_amount,
                            gain=bed_and_breakfast_gain,
                            allowable_cost=bed_and_breakfast_allowable_cost,
                            fees=fees,
                            bed_and_breakfast_date_index=search_index,
                            new_quantity=current_quantity,
                            new_pool_cost=current_amount,
                        )
                    )
                    # If we completely matched the current disposal,
                    # there's no need to keep looking for more B&B days
                    if disposal_quantity <= 0:
                        break
        if disposal_quantity > 0:
            available_quantity = disposal_quantity
            fees = disposal.fees * available_quantity / original_disposal_quantity

            # Multiply by available_quantity before divide to avoid rounding errors from division
            amount_delta = normalize_amount(
                (available_quantity * current_amount) / current_quantity
            )

            # No gain/no loss: deemed proceeds equal the allowable cost.
            r104_amount = (
                amount_delta if no_gain_no_loss else available_quantity * disposal_price
            )
            r104_proceeds = r104_amount + fees
            r104_allowable_cost = amount_delta + fees
            r104_gain = r104_proceeds - r104_allowable_cost
            chargeable_gain += r104_gain
            LOGGER.debug(
                "SECTION 104, quantity %s, gain %s, proceeds amount %s, "
                "allowable cost %s",
                available_quantity,
                r104_gain,
                r104_proceeds,
                r104_allowable_cost,
            )
            disposal_quantity -= available_quantity
            proceeds_amount -= available_quantity * disposal_price
            current_quantity -= available_quantity
            current_amount -= amount_delta
            if current_quantity == 0:
                assert round_decimal(current_amount, 10) == 0, (
                    f"current amount {current_amount}"
                )
            calculation_entries.append(
                CalculationEntry(
                    rule_type=(
                        RuleType.TRANSFER_TO_SPOUSE
                        if no_gain_no_loss
                        else RuleType.SECTION_104
                    ),
                    quantity=available_quantity,
                    amount=r104_amount,
                    gain=r104_gain,
                    allowable_cost=r104_allowable_cost,
                    fees=fees,
                    new_quantity=current_quantity,
                    new_pool_cost=current_amount,
                )
            )
            disposal_quantity = Decimal(0)

        assert round_decimal(disposal_quantity, 23) == 0, (
            f"disposal quantity {disposal_quantity}"
        )
        self.portfolio[symbol] = Position(
            current_quantity, normalize_amount(current_amount)
        )
        chargeable_gain = round_decimal(chargeable_gain, 2)
        return chargeable_gain, calculation_entries

    def process_rename(self, old: str, new: str) -> CalculationEntry:
        """Transfer pool from old ticker to new ticker (no disposal)."""
        pos = self.portfolio.pop(old, Position())
        self.portfolio[new] += pos
        return CalculationEntry(
            rule_type=RuleType.RENAME,
            quantity=pos.quantity,
            amount=Decimal(0),
            fees=Decimal(0),
            new_quantity=self.portfolio[new].quantity,
            new_pool_cost=self.portfolio[new].amount,
            allowable_cost=pos.amount,
            renamed_to=new,
        )

    def apply_split_openings(self, date_index: datetime.date) -> None:
        """Restate every holding a reorganisation touches today.

        A reorganisation restates the day's opening pool before any of the
        day's activity: the rows that preceded it are already in the units it
        leaves behind, so scaling after any of them applies the ratio twice.

        Replays what the first pass recorded rather than deciding again.
        Substituting the broker's count in one pass while scaling by the ratio
        in the other differs by a billionth of a share on the Trading 212
        exports this was built from, which is enough to leave a disposal of
        the whole holding short of zero.
        """
        renames = self.rename_list.get(date_index, {})
        for symbol, transformation in sorted(self.splits.get(date_index, {}).items()):
            chained = self._rename_chain_at(symbol, renames)
            if chained is not None:
                raise CalculationError(
                    self._rename_chain_message(symbol, date_index, *chained)
                )
            pooled = self._rename_pooling_with(symbol, date_index, renames)
            if pooled is not None:
                raise CalculationError(
                    self._rename_and_split_message(symbol, date_index, *pooled)
                )
            position = self.portfolio[symbol]
            if position.quantity != transformation.day_open_quantity:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: the holding opens at "
                    f"{strip_zeros(position.quantity)} units here and at "
                    f"{strip_zeros(transformation.day_open_quantity)} where "
                    "the reorganisation was worked out. Please report this: "
                    "the two passes over the history disagree."
                )
            self.portfolio[symbol] = Position(
                transformation.scaled_day_open_quantity, position.amount
            )

    def _holds_units_today(self, symbol: str, date_index: datetime.date) -> bool:
        """Whether a rename today could move units of ``symbol`` anywhere.

        Called from ``apply_split_openings``, before any of the day's rows, so
        the portfolio still holds the day-opening pool; an acquisition later
        today lands before the rename, which is applied at the end of the day.
        """
        if symbol in self.portfolio and self.portfolio[symbol].quantity != 0:
            return True
        return has_key(self.acquisition_list, date_index, symbol)

    @staticmethod
    def _rename_chain_at(
        symbol: str, renames: dict[str, str]
    ) -> tuple[tuple[str, str], tuple[str, str]] | None:
        """Return the two renames that move this holding twice in one day.

        A day's renames are applied in the order the input lists them, so a
        holding renamed onward from the name it was just renamed to ends
        wherever that order puts it, and swapping the two rows moves it
        somewhere else. A date carries no order, so neither reading is
        established and there is nothing to pick between them. A cycle is the
        same thing joined up, and is caught by the same test.
        """
        next_name = renames.get(symbol)
        if next_name is None or next_name not in renames:
            return None
        return (symbol, next_name), (next_name, renames[next_name])

    def _rename_pooling_with(
        self,
        symbol: str,
        date_index: datetime.date,
        renames: dict[str, str],
    ) -> tuple[str, tuple[str, str]] | None:
        """Return a second holding the day's renames would pool this one with.

        A rename says the two tickers are one security, so a reorganisation of
        either restates both. The calculator restates one, and the renames
        bring whatever else they reach in afterwards, untouched. What they
        reach is not only the name at the other end of a rename this holding
        is named in: a holding renamed into the name this one is renamed to,
        or renamed along to it through names it never held, arrives just the
        same. So the whole of the day's rename graph connected to this holding
        is walked, in both directions, and any name on it that holds something
        of its own is the answer. The ordinary ticker change reaches only
        empty names, so it passes.

        Returns the holding it would be pooled with and the rename that
        reaches it, taking the renames in the order the input lists them.
        """
        reached = {symbol}
        frontier = [symbol]
        while frontier:
            name = frontier.pop(0)
            for old_name, new_name in renames.items():
                other = (
                    new_name
                    if name == old_name
                    else old_name
                    if name == new_name
                    else None
                )
                if other is None or other in reached:
                    continue
                if self._holds_units_today(other, date_index):
                    return other, (old_name, new_name)
                reached.add(other)
                frontier.append(other)
        return None

    def record_split_reconciliations(
        self,
        date_index: datetime.date,
        tax_year_start_index: datetime.date,
        calculation_log: CalculationLog,
    ) -> None:
        """Reconcile every reorganisation today to the broker's own count.

        Runs after the day's genuine acquisitions, so the correction cannot
        make a holding acquired earlier today look momentarily negative, and
        before its disposals, so a sale of the whole holding sees the count
        the broker stated.

        The delta is quantity only. It absorbs the broker's own rounding of
        the count it reported, and the difference left by restating the
        opening pool and the day's earlier rows separately rather than as one
        sum. It buys nothing and sells nothing, so it changes no cost and
        takes no part in same-day or 30-day identification.
        """
        for symbol, transformation in sorted(self.splits.get(date_index, {}).items()):
            position = self.portfolio[symbol]
            quantity = position.quantity + transformation.reconciliation_delta
            if quantity < 0:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: reconciling to the broker's count leaves "
                    f"{strip_zeros(quantity)} units."
                )
            self.portfolio[symbol] = Position(quantity, position.amount)
            if date_index < tax_year_start_index:
                continue
            event = transformation.event
            calculation_log[date_index][f"split${symbol}"] = [
                CalculationEntry(
                    rule_type=RuleType.STOCK_SPLIT,
                    # A reorganisation acquires and disposes of nothing; the
                    # counts it moves between two unit systems are in the
                    # detail below.
                    quantity=Decimal(0),
                    amount=Decimal(0),
                    fees=Decimal(0),
                    gain=Decimal(0),
                    allowable_cost=Decimal(0),
                    new_quantity=quantity,
                    new_pool_cost=position.amount,
                    stock_split=StockSplitDetail(
                        ratio_description=transformation.ratio_description,
                        broker_before_quantity=event.before_quantity,
                        broker_after_quantity=event.after_quantity,
                        day_open_quantity=transformation.day_open_quantity,
                        scaled_day_open_quantity=(
                            transformation.scaled_day_open_quantity
                        ),
                        quantity_before_reconciliation=position.quantity,
                        quantity_after_reconciliation=quantity,
                        reconciliation_delta=transformation.reconciliation_delta,
                    ),
                )
            ]

    def verify_split_day_closes(self, date_index: datetime.date) -> None:
        """Check every reorganisation today left the holding where it said.

        A holding renamed today is checked under the name it was renamed to:
        the rename has moved the pool by now. That single hop is the whole
        journey, and the pool arrived alone, because a day that renames the
        holding onward again or pools it with a second one is refused before
        any of this (see ``apply_split_openings``).
        """
        renames = self.rename_list.get(date_index, {})
        for symbol, transformation in sorted(self.splits.get(date_index, {}).items()):
            closing = self.portfolio[renames.get(symbol, symbol)].quantity
            if closing != transformation.expected_day_close:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: the day closes at {strip_zeros(closing)} "
                    "units where the reorganisation and the day's activity "
                    f"come to {strip_zeros(transformation.expected_day_close)}."
                    " Please report this: the two passes over the history "
                    "disagree."
                )

    @staticmethod
    def _rename_chain_message(
        symbol: str,
        date_index: datetime.date,
        first: tuple[str, str],
        second: tuple[str, str],
    ) -> str:
        """Explain why a holding renamed twice in a day cannot be restated."""
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: it "
            f"is renamed to {first[1]}, and {second[0]} is renamed to "
            f"{second[1]}, the same day. The renames are applied in the order "
            "the input lists them, so where the holding ends up, and what it "
            "is pooled with on the way, depends on which of the two rows "
            "comes first. A date carries no order, so neither reading is "
            "established. Put the renames on the days they happened, or work "
            "this day out by hand (consider professional advice)."
        )

    @staticmethod
    def _rename_and_split_message(
        symbol: str,
        date_index: datetime.date,
        other: str,
        rename: tuple[str, str],
    ) -> str:
        """Explain why a rename that pools two holdings cannot be applied."""
        old_name, new_name = rename
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"the day's renames pool it with {other}, which holds shares of "
            f"its own, by way of the rename of {old_name} to {new_name}. A "
            "rename says the two tickers are one security, so the "
            "reorganisation restates both, but it is applied under one name "
            "and the renames bring the other holding in afterwards, "
            "untouched. Whether those shares were part of the event depends on "
            "whether the renames took effect before it or after, and the input "
            "carries dates but not that order. Work this day out by hand "
            "(consider professional advice)."
        )

    @staticmethod
    def _two_renames_message(
        old_symbol: str,
        date_index: datetime.date,
        first: str,
        second: str,
    ) -> str:
        """Explain why one ticker cannot be renamed to two names in a day."""
        return (
            f"{old_symbol} is renamed to both {first} and {second} on "
            f"{date_index}. The holding goes to one name or the other, and a "
            "date carries no order to choose by, so which one cannot be "
            "established and the pool would be replayed under a name it never "
            "reached. Leave the row that does not belong out, or work this "
            "day out by hand (consider professional advice)."
        )

    def _two_splits_message(
        self,
        symbol: str,
        date_index: datetime.date,
        rows: list[BrokerTransaction],
    ) -> str:
        """Explain why two reorganisations of one holding cannot be combined."""
        listed = "\n".join(f"  {row}" for row in rows)
        if any(isinstance(row, RawTransaction) for row in rows):
            # A single RAW row settles a day the brokers also reported, so
            # more than one of them is the thing to fix rather than to add.
            remedy = (
                "Leave all but one of the RAW STOCK_SPLIT rows out, so that "
                "one row states the change to the whole holding"
            )
        else:
            remedy = (
                "Add a single RAW STOCK_SPLIT row stating the change to the "
                "whole holding, which settles the day and is used in place of "
                "the broker rows"
            )
        return (
            f"{symbol} has more than one reorganisation on {date_index}:\n"
            f"{listed}\n"
            "The Section 104 holding is global, so a corporate ratio applies "
            "to it once. Two brokers reporting the same event is not the same "
            "as two events, and this tool cannot tell them apart, so it will "
            f"not guess. {remedy}, or work this day out by hand (consider "
            "professional advice)."
        )

    def _split_chronology_message(
        self,
        symbol: str,
        date_index: datetime.date,
        row: BrokerTransaction,
        other: BrokerTransaction,
    ) -> str:
        """Explain why a same-day row cannot be placed around a reorganisation."""
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"this row is on the same day and cannot be placed either side of "
            f"it, so there is no telling whether its count is stated in the "
            f"units before or after:\n  {other}\n"
            f"The reorganisation is:\n  {row}\n"
            "The two come from different inputs, or from an export that gives "
            "neither times nor a documented order, or the row falls between "
            "the two halves of the reorganisation. Run again with the day's "
            f"{symbol} rows in one input that states times, or work the day "
            "out by hand (consider professional advice)."
        )

    @staticmethod
    def _unresolved_same_day_message(
        event: StockSplitEvent, other: BrokerTransaction
    ) -> str:
        """Explain why an unrecoverable ratio cannot restate an earlier row."""
        assert isinstance(event.ratio, UnresolvedRatio)
        return (
            f"Cannot apply the reorganisation of {event.describe()}: "
            f"{event.symbol} also changed hands earlier that day:\n  {other}\n"
            "That row's count is in the units before the reorganisation, so it "
            "has to be restated into the units after it, and the ratio for "
            f"that cannot be recovered from the export: {event.ratio.describe()}. "
            "Run again with the day's other rows left out, or work the day out "
            "by hand (consider professional advice)."
        )

    @staticmethod
    def _unresolved_scaling_message(event: StockSplitEvent) -> str:
        """Explain why an unrecoverable ratio cannot scale the pool."""
        assert isinstance(event.ratio, UnresolvedRatio)
        return (
            f"Cannot apply the reorganisation of {event.describe()}: the "
            f"holding is not the {strip_zeros(event.before_quantity)} units "
            "the export states, so it has to be scaled by the corporate ratio "
            f"rather than replaced, and that ratio cannot be recovered from "
            f"the export: {event.ratio.describe()}. This happens when the same "
            "security is held through more than one broker. Work the "
            "reorganisation out by hand (consider professional advice)."
        )

    @staticmethod
    def _broker_split_delta_message(
        symbol: str,
        date_index: datetime.date,
        account: str,
        others: list[str],
    ) -> str:
        """Explain why one account's split delta is not a global one."""
        listed = ", ".join(others)
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: it "
            f"is a single row from {account} stating how many units that "
            "account gained or lost, and this holding also has units from "
            f"{listed}. Adding one account's change to a holding built from "
            "several derives a ratio that was never the corporate one. Supply "
            "the reorganisation as a RAW STOCK_SPLIT row stating the change to "
            "the whole holding, or as a broker export that states the counts "
            "either side of the event."
        )

    @staticmethod
    def _unresolved_bnb_message(
        event: StockSplitEvent,
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
    ) -> str:
        """Explain why an unrecoverable ratio blocks a bed and breakfast match."""
        assert isinstance(event.ratio, UnresolvedRatio)
        return (
            f"Cannot compute the disposal of {symbol} on {date_index}: it is "
            f"identified under the 30-day rule against the acquisition on "
            f"{search_index}, and {event.describe()} lies between them, so the "
            "two counts are in different units. Converting one to the other "
            "needs the corporate ratio, and that cannot be recovered from the "
            f"export: {event.ratio.describe()}. Work this disposal out by hand "
            "(consider professional advice)."
        )

    def _spin_off_pool(self, date_index: datetime.date, source: str, dest: str) -> str:
        """Return the symbol whose pool a spin-off from `source` draws on today.

        A rename on the day is applied after the day's acquisitions, so a
        source renamed that morning still has its pool under the old name
        here.

        Refuses when the source was also traded on the day. Whether a trade
        came before or after the reorganisation decides which shares were
        reorganised and what they cost, and the input carries dates but not
        times, so there is no right answer to give. Shares the source itself
        received by spin-off that day are not a trade; dependency order has
        already put them in its pool.
        """
        renames = self.rename_list.get(date_index, {})
        # Follow the day's renames back from the source, a holding renamed
        # twice in a morning being two hops from its pool. Only renames on
        # this chain matter; what happened to other symbols that day does not.
        names = [source]
        frontier = [source]
        while frontier:
            current = frontier.pop()
            for old, new in renames.items():
                if new != current:
                    continue
                if old in names:
                    raise CalculationError(
                        f"Cannot compute the spin-off of {dest} from {source} on "
                        f"{date_index}: the day's renames go round in a circle "
                        f"({' -> '.join([*names, old])}), so there is no telling "
                        f"where the pool is. Work {source} and {dest} out by "
                        "hand (consider professional advice)."
                    )
                names.append(old)
                frontier.append(old)
        # The pool sits under whichever of these names holds anything here,
        # since the day's renames are applied after its acquisitions. Cost
        # with no shares counts: a fee charged after a holding was sold out
        # leaves exactly that, and the rename merges it in all the same. Two
        # of them means two holdings became one that day, and whether the
        # spin-off applied to both or to one cannot be told from the input.
        holding = sorted(
            name
            for name in names
            if name in self.portfolio
            and (self.portfolio[name].quantity > 0 or self.portfolio[name].amount != 0)
        )
        if len(holding) > 1:
            raise CalculationError(
                f"Cannot compute the spin-off of {dest} from {source} on "
                f"{date_index}: {' and '.join(holding)} were separate holdings "
                "until renamed into one that day, and whether the spin-off "
                "applied to both or just one cannot be told from the input. "
                f"Work {source} and {dest} out by hand (consider professional "
                "advice)."
            )
        pool = holding[0] if holding else source
        activity = []
        for name in names:
            spun_in = sum(
                (
                    spin_off.quantity
                    for spin_off in self.spin_offs.get(date_index, [])
                    if spin_off.dest == name
                ),
                Decimal(0),
            )
            if (
                has_key(self.acquisition_list, date_index, name)
                and self.acquisition_list[date_index][name].quantity > spun_in
            ):
                activity.append("bought")
            if has_key(self.disposal_list, date_index, name):
                activity.append("sold")
            if has_key(self.transfer_to_spouse_list, date_index, name):
                activity.append("transferred to a spouse")
            if (date_index, name) in self.fee_days:
                activity.append("charged a fee")
        if activity:
            raise CalculationError(
                f"Cannot compute the spin-off of {dest} from {source} on "
                f"{date_index}: {source} was also {' and '.join(activity)} that "
                "day. Whether that came before or after the reorganisation "
                "decides which shares were reorganised and what they cost, and "
                "the input has dates but not times, so this tool cannot tell. "
                f"Work {source} and {dest} out by hand for this period (consider "
                "professional advice). Do not change the dates."
            )
        return pool

    def _acquisition_order(self, date_index: datetime.date) -> list[str]:
        """Return the day's acquired symbols, each spun-off holding after its source.

        A spin-off takes its share of the source's pool as it stands on the
        day, and the day's purchases of the source are part of that, since
        same-day acquisitions form a single acquisition (TCGA 1992 s105). So
        every other symbol is pooled first, and a spun-off holding only after
        whatever it was spun off from, so that a holding spun off and spun off
        from on the same day is complete before it is apportioned. Holdings
        spun off from the same source take their shares one after another, so
        among themselves they keep the order the spin-offs happened in, which
        is the order the first pass worked them out in.
        """
        sources: dict[str, list[str]] = defaultdict(list)
        first_event: dict[str, int] = {}
        for index, spin_off in enumerate(self.spin_offs.get(date_index, [])):
            sources[spin_off.dest].append(spin_off.source)
            first_event.setdefault(spin_off.dest, index)

        def depth(symbol: str) -> int:
            # A cycle cannot get here: the first pass refuses a chain that is
            # not in order, and a cycle is never in order.
            return 1 + max((depth(source) for source in sources[symbol]), default=-1)

        return sorted(
            self.acquisition_list[date_index],
            key=lambda symbol: (depth(symbol), first_event.get(symbol, -1)),
        )

    def _match_across_splits(
        self,
        disposal_quantity: Decimal,
        available_acquisition_units: Decimal,
        cumulative_ratio: Fraction,
        *,
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
    ) -> tuple[Decimal, Decimal]:
        """Match a disposal against an acquisition counted in other units.

        Returns what the disposal takes, in its own units, and what that
        consumes of the acquisition, in the acquisition's. Everything
        reserved is counted in the acquisition's own units, so the whole
        remainder converts once: converting only the acquisition would
        subtract post-split counts from a pre-split one and can go negative.

        Both counts are kept to ten decimal places, so a large enough ratio
        can leave a real quantity with nothing to say for itself on the other
        side. That is refused rather than matched at nil cost against an
        acquisition it then fails to reserve.
        """
        available_disposal_units = unscale_quantity(
            available_acquisition_units, cumulative_ratio
        )
        if available_disposal_units <= 0:
            raise CalculationError(
                self._unrepresentable_match_message(
                    symbol, date_index, search_index, cumulative_ratio
                )
            )
        matched = min(disposal_quantity, available_disposal_units)
        # Taking the whole remainder outright, rather than converting it back,
        # keeps a ten-decimal round trip from leaving or over-consuming a
        # residual share.
        if matched == available_disposal_units:
            consumed = available_acquisition_units
        else:
            consumed = min(
                available_acquisition_units,
                scale_quantity(matched, cumulative_ratio),
            )
        if consumed <= 0:
            raise CalculationError(
                self._unrepresentable_match_message(
                    symbol, date_index, search_index, cumulative_ratio
                )
            )
        assert consumed <= available_acquisition_units
        return matched, consumed

    @staticmethod
    def _unrepresentable_match_message(
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
        cumulative_ratio: Fraction,
    ) -> str:
        """Explain a match the two unit systems cannot both express."""
        return (
            f"Cannot compute the disposal of {symbol} on {date_index}: it is "
            f"identified under the 30-day rule against the acquisition on "
            f"{search_index}, and the reorganisations in between restate the "
            f"count by {cumulative_ratio}. One of the two quantities comes to "
            "less than the ten decimal places this keeps, so the match cannot "
            "be expressed in both unit systems at once. Work this disposal out "
            "by hand (consider professional advice)."
        )

    def _matchable_acquisition(
        self,
        date_index: datetime.date,
        symbol: str,
    ) -> HmrcTransactionData:
        """Return the acquisitions a disposal could be identified with.

        Units a reorganisation restated are not here to be taken out: they
        were never acquired (TCGA 1992 s127, CG51805), so they never enter
        ``acquisition_list`` in the first place.
        """
        if not has_key(self.acquisition_list, date_index, symbol):
            return HmrcTransactionData()
        return self.acquisition_list[date_index][symbol]

    def _contending_acquisitions(
        self,
        symbol: str,
        date_index: datetime.date,
        bnb_claimed_before_today: dict[tuple[datetime.date, str], Decimal],
    ) -> list[datetime.date]:
        """Dates a same-day sale and transfer of ``symbol`` would both claim.

        Left to the Section 104 pool alone the two draw at the same average
        cost, so the order between them cannot change either result. They only
        contend over acquisitions both could be identified against under the
        same-day or 30-day rules, and then HMRC does not say which one gets
        them. Returns those acquisition dates, earliest first, or an empty list
        when there is nothing to argue over.
        """
        dates = []
        effective_symbol = symbol

        def has_shares_going_spare(
            day: datetime.date, ticker: str, *, is_disposal_day: bool
        ) -> bool:
            # Only what is left over is worth arguing about. Shares from a
            # split are already excluded, and a management fee is recorded as
            # cost with no shares at all.
            acquisition = self._matchable_acquisition(day, ticker)
            if acquisition.quantity <= 0 or acquisition.amount == 0:
                return False
            # Claims an earlier disposal already made are settled and leave
            # that much less to argue over. What the sale we are contending
            # with took today is deliberately not deducted: those are the very
            # shares in dispute, and it only got them by going first.
            taken = bnb_claimed_before_today.get((day, ticker), Decimal(0))
            if not is_disposal_day:
                # Whatever that later day disposes of under the same-day rule
                # is spoken for before either of ours can reach it. On the
                # disposal day itself the sale and the transfer are the two
                # laying claim, so subtracting them would hide the clash.
                for log in (self.disposal_list, self.transfer_to_spouse_list):
                    if has_key(log, day, ticker):
                        taken += log[day][ticker].quantity
            return acquisition.quantity - taken > 0

        if has_shares_going_spare(date_index, symbol, is_disposal_day=True):
            dates.append(date_index)
        for i in range(BED_AND_BREAKFAST_DAYS):
            search_index = date_index + datetime.timedelta(days=i + 1)
            # HMRC treats renames as the same security for B&B purposes.
            effective_symbol = self.rename_list.get(search_index, {}).get(
                effective_symbol, effective_symbol
            )
            if has_shares_going_spare(
                search_index, effective_symbol, is_disposal_day=False
            ):
                dates.append(search_index)
        return dates

    def _same_day_sale_and_transfer_message(
        self,
        symbol: str,
        date_index: datetime.date,
        contended: list[datetime.date],
    ) -> str:
        """Explain why a same-day sale and transfer cannot be calculated."""
        sold = self.disposal_list[date_index][symbol].quantity
        transferred = self.transfer_to_spouse_list[date_index][symbol].quantity
        shown = ", ".join(str(date) for date in contended[:MAX_CONTENDED_DATES_SHOWN])
        if len(contended) > MAX_CONTENDED_DATES_SHOWN:
            shown += f" and {len(contended) - MAX_CONTENDED_DATES_SHOWN} more"
        has_gift = (date_index, symbol) in self.gift_disposals
        has_sale = (date_index, symbol) in self.sale_days
        if has_gift and has_sale:
            verb, noun = ("sold or gave away", "disposal")
        elif has_gift:
            verb, noun = ("gave away", "gift")
        else:
            verb, noun = ("sold", "sale")
        return (
            f"On {date_index} you {verb} {strip_zeros(sold)} units of {symbol} "
            f"and transferred {strip_zeros(transferred)} to a spouse, and you "
            f"also acquired {symbol} on {shown}.\n"
            f"Both the {noun} and the transfer have to be identified against those "
            "acquisitions under the same-day and 30-day rules, but they cannot "
            "share them: TCGA 1992 s105(1) treats everything disposed of on one "
            "day as a single transaction, and here one part is chargeable while "
            "the other is no gain/no loss. HMRC does not say how to split the "
            "acquisitions between the two, so this tool will not guess.\n"
            "What to do: work this disposal out by hand and consider taking "
            "professional advice, then leave the affected symbol out of the "
            "input and add its figures to your return yourself. Everything else "
            "in your history still calculates normally.\n"
            "Do not move the real transaction dates to get past this: the dates "
            "are what the identification rules run on, so changing them changes "
            "the tax."
        )

    def _record_transfers_to_spouse(
        self,
        date_index: datetime.date,
        tax_year_start_index: datetime.date,
        calculation_log: CalculationLog,
        bnb_claimed_before_today: dict[tuple[datetime.date, str], Decimal],
    ) -> None:
        """Process and log no gain/no loss transfers to spouse for a single day.

        Each transfer is identified against the transferor's own acquisitions
        exactly like a disposal (see ``process_disposal``), but with a nil gain,
        so it is kept out of the taxable disposal totals.
        """
        if date_index not in self.transfer_to_spouse_list:
            return
        for symbol in self.transfer_to_spouse_list[date_index]:
            # HMRC does not define how to identify a taxable sale and a no gain/no
            # loss transfer of the same shares on the same day, so refuse the
            # cases where that identification actually changes the answer.
            if has_key(self.disposal_list, date_index, symbol) and (
                contended := self._contending_acquisitions(
                    symbol, date_index, bnb_claimed_before_today
                )
            ):
                raise CalculationError(
                    self._same_day_sale_and_transfer_message(
                        symbol, date_index, contended
                    )
                )
            gain, entries = self.process_disposal(
                symbol, date_index, no_gain_no_loss=True
            )
            assert gain == 0, gain
            base_cost = sum((entry.allowable_cost for entry in entries), Decimal(0))
            # Surface transfers made in the reported period; the rest of the
            # history walk logs them at DEBUG.
            LOGGER.log(
                logging.INFO if self.date_in_tax_year(date_index) else logging.DEBUG,
                "Transferred %s units of %s to spouse on %s "
                "(no gain/no loss, base cost £%s)",
                self.transfer_to_spouse_list[date_index][symbol].quantity,
                symbol,
                date_index,
                round_decimal(base_cost, 2),
            )
            if date_index >= tax_year_start_index:
                calculation_log[date_index][f"transfer-to-spouse${symbol}"] = entries

    def process_eri(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> CalculationEntry | None:
        """Process single excess reported income."""
        eri = self.get_eri(symbol, date_index)
        assert eri is not None
        amount = self.portfolio[eri.symbol].amount
        quantity = self.portfolio[eri.symbol].quantity

        if quantity == 0:
            return None

        allowable_cost = quantity * eri.price

        if allowable_cost == 0:
            return None

        new_amount = amount + allowable_cost
        LOGGER.debug(
            "Detected excess reported income of %s on %s, "
            "modyfing the cost amount from %s to %s",
            eri.symbol,
            eri.date,
            amount,
            new_amount,
        )
        self.portfolio[eri.symbol].amount = new_amount

        if self.date_in_tax_year(eri.distribution_date):
            self.eris_distribution[eri.distribution_date][symbol] += (
                ExcessReportedIncomeDistribution(
                    price=eri.price,
                    amount=allowable_cost,
                    quantity=quantity,
                )
            )

        return CalculationEntry(
            RuleType.EXCESS_REPORTED_INCOME,
            quantity=quantity,
            amount=-amount,
            new_quantity=quantity,
            gain=None,
            fees=Decimal(0),
            new_pool_cost=new_amount,
            allowable_cost=allowable_cost,
            eris=[eri],
        )

    def _group_by_month(
        self,
        entries: dict[tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount],
    ) -> dict[tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount]:
        """Group in-tax-year amounts by month, keyed by the month's last date."""
        monthly: dict[
            tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount
        ] = defaultdict(ForeignCurrencyAmount)
        last_date: datetime.date = datetime.date.min
        last_broker: str | None = None
        last_currency: CurrencyCode | None = None

        for (broker, currency, date), foreign_amount in sorted(entries.items()):
            if not self.date_in_tax_year(date):
                continue
            if (
                broker == last_broker
                and currency == last_currency
                and (date.year, date.month) == (last_date.year, last_date.month)
            ):
                monthly[broker, currency, date] = monthly.pop(
                    (broker, currency, last_date)
                )
            monthly[broker, currency, date] += foreign_amount
            last_date = date
            last_broker = broker
            last_currency = currency
        return monthly

    def process_interests(self) -> None:
        """Process all interest events.

        It groups them by month, using the last date on each month for the report
        and updates the interest totals for the year.
        """
        monthly_interests = self._group_by_month(self.interest_list)

        for (broker, currency, date), foreign_amount in monthly_interests.items():
            gbp_amount = self.currency_converter.to_gbp(
                foreign_amount.amount, currency, date
            )
            if currency == UK_CURRENCY:
                self.total_uk_interest += gbp_amount
                rule_prefix = "interestUK"
            else:
                self.total_foreign_interest += gbp_amount
                rule_prefix = f"interest{currency}"

            self.calculation_log_yields[date][f"{rule_prefix}${broker}"] = [
                CalculationEntry(
                    rule_type=RuleType.INTEREST,
                    quantity=Decimal(1),
                    amount=gbp_amount,
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                    fees=Decimal(0),
                )
            ]

        monthly_interest_taxes = self._group_by_month(self.interest_tax_list)

        for (broker, currency, date), foreign_amount in monthly_interest_taxes.items():
            gbp_amount = self.currency_converter.to_gbp(
                foreign_amount.amount, currency, date
            )
            # Withholding rows are negative, so negate rather than abs():
            # positive reversal rows then cancel out across months.
            tax_amount = -gbp_amount
            rule_prefix = f"interestTax{currency}"
            self.total_interest_tax += tax_amount

            self.calculation_log_yields[date][f"{rule_prefix}${broker}"] = [
                CalculationEntry(
                    rule_type=RuleType.INTEREST_TAX,
                    quantity=Decimal(1),
                    amount=tax_amount,
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                    fees=Decimal(0),
                )
            ]

    def dividend_source_country(
        self, symbol: str, currency: CurrencyCode
    ) -> str | None:
        """Return the country a dividend was paid from, or None if unknown.

        The ISIN a security is registered under is the authority. Where no
        parser supplied one, fall back to guessing from the currency, which is
        what the calculator did before ISINs were consulted.
        """
        isin = self.isin_converter.get_symbol_to_isin_map().get(symbol)
        if isin is not None:
            return isin[:ISIN_COUNTRY_CODE_LENGTH]
        return DIVIDEND_CURRENCY_TO_COUNTRY.get(currency)

    def _warn_unattributed_dividend_tax(
        self,
        symbol: str,
        date: datetime.date,
        tax: ForeignCurrencyAmount,
        candidates: list[datetime.date],
    ) -> None:
        """Warn about withheld tax that no single dividend can claim."""
        # The tax is missing from the report of every year that holds a
        # payment which could have carried it, not only from the year it was
        # taken in, and each of those years is entitled to hear so. A
        # withholding just after 5 April can leave a payment short on either
        # side of the boundary.
        if not any(self.date_in_tax_year(affected) for affected in (date, *candidates)):
            return
        # Tax below half a unit would print as a bare "-0.00", so it is
        # shown as it stands instead. Whether it is material is a question
        # about its value in GBP, and answering it here would put a rate
        # lookup, and the failure of one, in the way of a warning.
        amount = round_decimal(tax.amount, 2) or tax.amount
        if candidates:
            LOGGER.warning(
                "Dividend tax of %s %s for %s on %s could have been taken "
                "from the dividend on %s, so it is left out of the report! "
                "Date it in the export on the one it belongs to to have it "
                "counted.",
                amount,
                tax.currency,
                symbol,
                date,
                " or ".join(str(candidate) for candidate in candidates),
            )
            return
        LOGGER.warning(
            "Dividend tax of %s %s for %s on %s has no dividend in the %d "
            "days before it or the %d days after, so it is left out of the "
            "report!",
            amount,
            tax.currency,
            symbol,
            date,
            DIVIDEND_TAX_MATCH_DAYS,
            DIVIDEND_TAX_LEAD_DAYS,
        )

    @staticmethod
    def _dividends_for_tax(
        date: datetime.date, dividend_dates: set[datetime.date]
    ) -> list[datetime.date]:
        """Return the dividends a withholding could have been taken from.

        A payment on the day of the withholding is not in doubt, whatever
        else lies nearby. Otherwise, every payment in the window counts as a
        candidate, and it is for the caller to refuse when there is more
        than one: which of two payments a correction belongs to cannot be
        told from a date and a net amount.

        The window is asymmetric because the directions mean different
        things. Tax follows the payment it was taken from, and a correction
        follows it by longer, so it reaches back weeks; a payment after the
        withholding only means the broker posted the tax early, which it
        does by days.
        """
        if date in dividend_dates:
            return [date]
        return sorted(
            candidate
            for candidate in dividend_dates
            if -DIVIDEND_TAX_MATCH_DAYS
            <= (candidate - date).days
            <= DIVIDEND_TAX_LEAD_DAYS
        )

    def _attribute_dividend_tax(self) -> DividendTaxAttribution:
        """Attribute each withholding to the dividend it was taken from.

        Brokers do not always date a withholding, or a later correction of
        one such as a Schwab ``NRA Tax Adj``, on the day of the payment it
        belongs to, so tax is matched to a dividend of the same broker and
        symbol nearby. Holding one symbol at two brokers therefore keeps
        each broker's withholding with that broker's own payments.

        Tax is attributed only where one payment can claim it. Where two
        could, the export does not record which, and no arrangement of dates
        and amounts recovers it: an adjustment reaching back to last month's
        payment looks exactly like ordinary tax on next month's. Those are
        left out of the report and warned about instead of guessed at. They
        still reach the summary, and dating one on its dividend in the input
        settles it.

        Matching runs over the whole history rather than the reported year,
        so a payment and its withholding either side of 5 April stay
        together, and one withholding cannot be claimed in two years.
        """
        matched: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        unmatched: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        for (broker, symbol, date), tax in self.dividend_tax_list.items():
            if not tax.amount:
                continue
            candidates = self._dividends_for_tax(
                date, self.dividend_dates.get((broker, symbol), set())
            )
            if len(candidates) == 1:
                matched[symbol, candidates[0]] += tax
                continue
            # Either nothing is near enough, or two payments are and the
            # export does not say which one this is. Guessing between them
            # puts a wrong figure in the report; leaving it out and saying so
            # does not.
            self._warn_unattributed_dividend_tax(symbol, date, tax, candidates)
            unmatched[symbol, date] += tax
        return DividendTaxAttribution(matched, unmatched)

    def _dividend_tax_attribution(self) -> DividendTaxAttribution:
        """Attribute withholding once, for both the summary and the report.

        The two must agree, and attributing twice would warn twice.
        """
        if self._attributed_dividend_tax is None:
            self._attributed_dividend_tax = self._attribute_dividend_tax()
        return self._attributed_dividend_tax

    def process_dividends(self) -> None:
        """Process all dividend events and taxes.

        It updates the interest total for the year if needed.
        """
        attributed_tax = self._dividend_tax_attribution().matched
        for (symbol, date), foreign_amount in self.dividend_list.items():
            # Dividends outside the computed tax year are not reported, so
            # neither are the warnings raised while resolving their treaty.
            if not self.date_in_tax_year(date):
                continue

            # Read without inserting: a dividend with no tax must not leave a
            # zero entry behind in the attribution.
            tax = attributed_tax.get((symbol, date), ForeignCurrencyAmount())
            currency = foreign_amount.currency
            assert currency is not None, f"Dividend for {symbol} has no currency"

            # The tax is converted at the dividend's rate below, so the two
            # have to be in one currency whichever way the tax runs and
            # whatever the holding is. Checking this only for tax deducted
            # from an ordinary holding let a refund, or any tax on a bond
            # fund, be converted as though it were in the dividend's
            # currency.
            if tax.amount and tax.currency != currency:
                raise CalculationError(
                    f"Dividend and withholding tax currencies do not match "
                    f"for {symbol} on {date}: dividend "
                    f"{foreign_amount.currency}, tax {tax.currency}"
                )

            treaty = None
            is_interest_fund = symbol in self.interest_fund_tickers
            if tax.amount < 0:
                if is_interest_fund:
                    LOGGER.warning(
                        "Cannot apply taxation treaty for bond fund %s", symbol
                    )
                else:
                    country = self.dividend_source_country(symbol, currency)
                    if country is None:
                        LOGGER.warning(
                            "Source country of the %s dividend is unknown (ticker: %s), "
                            "double taxation rules cannot be determined!",
                            currency,
                            symbol,
                        )
                    elif country not in DIVIDEND_DOUBLE_TAXATION_RULES:
                        LOGGER.warning(
                            "Taxation treaty for %s country is missing (ticker: %s), "
                            "double taxation rules cannot be determined!",
                            country,
                            symbol,
                        )
                    else:
                        treaty = DIVIDEND_DOUBLE_TAXATION_RULES[country]
                        expected_tax = treaty.country_rate * -foreign_amount.amount
                        if not approx_equal(expected_tax, tax.amount):
                            LOGGER.warning(
                                "Determined double taxation treaty does not match the "
                                "base taxation rules (expected %.2f base tax for %s "
                                "but %.2f was deducted) for %s ticker!",
                                expected_tax,
                                treaty.country,
                                tax.amount,
                                symbol,
                            )
                            treaty = None

            amount = self.currency_converter.to_gbp(
                foreign_amount.amount, currency, date
            )
            tax_amount = self.currency_converter.to_gbp(tax.amount, currency, date)

            dividend = Dividend(
                date=date,
                symbol=symbol,
                amount=amount,
                tax_at_source=tax_amount,
                is_interest=is_interest_fund,
                tax_treaty=treaty,
            )

            self.calculation_log_yields[date][f"dividend${symbol}"] = [
                CalculationEntry(
                    rule_type=RuleType.DIVIDEND,
                    quantity=Decimal(1),
                    amount=amount,
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                    fees=Decimal(0),
                    dividend=dividend,
                )
            ]

            if is_interest_fund:
                self.total_foreign_interest += amount

    def _warn_unmatched_cgt_exempt_tickers(self) -> None:
        """Warn for any exempt ticker with no transactions."""
        logged_symbols = {
            sym.upper()
            for log in (self.acquisition_list, self.disposal_list)
            for day in log.values()
            for sym in day
        }
        for ticker in sorted(self.cgt_exempt_tickers):
            if ticker not in logged_symbols:
                LOGGER.warning(
                    "CGT-exempt ticker '%s' was not found in acquisitions or disposals",
                    ticker,
                )

    def calculate_capital_gain(
        self,
    ) -> CapitalGainsReport:
        """Calculate capital gain and return generated report.

        Runs once per calculator. The walk consumes the first pass's
        estimates as it replaces them and accumulates bed and breakfast
        claims as it makes them, so a second run would count both twice.
        """
        if self.calculated:
            raise RuntimeError(
                "calculate_capital_gain() runs once per calculator; build a "
                "new one to calculate again"
            )
        self.calculated = True
        self._warn_unmatched_cgt_exempt_tickers()
        begin_index = INTERNAL_START_DATE
        tax_year_start_index = self.tax_year_start_date
        end_index = self.tax_year_end_date
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        gift_loss = Decimal(0)
        exempt_disposal_count = 0
        exempt_disposal_proceeds = Decimal(0)

        # The first pass left its estimates in the portfolio; the walk rebuilds
        # it from nothing.
        self.portfolio.clear()
        self.spin_off_entries.clear()
        calculation_log: CalculationLog = defaultdict(dict)

        for date_index in (
            begin_index + datetime.timedelta(days=x)
            for x in range((end_index - begin_index).days + 1)
        ):
            # What earlier disposals have already bed-and-breakfasted, captured
            # before today's own disposals add to it. Only those earlier claims
            # settle whether a later acquisition is still up for grabs.
            bnb_claimed_before_today = (
                {
                    (day, ticker): data.quantity
                    for day, tickers in self.bnb_list.items()
                    for ticker, data in tickers.items()
                }
                if date_index in self.transfer_to_spouse_list
                else {}
            )
            self.apply_split_openings(date_index)
            if date_index in self.acquisition_list:
                for symbol in self._acquisition_order(date_index):
                    calculation_entries = self.process_acquisition(
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"buy${symbol}"] = (
                            calculation_entries
                        )
            for source, entries in self.spin_off_entries.pop(date_index, {}).items():
                if date_index >= tax_year_start_index:
                    calculation_log[date_index][f"spin-off${source}"] = entries
            self.record_split_reconciliations(
                date_index, tax_year_start_index, calculation_log
            )
            if date_index in self.disposal_list:
                for symbol in self.disposal_list[date_index]:
                    transaction_capital_gain, calculation_entries = (
                        self.process_disposal(symbol, date_index)
                    )
                    if date_index >= tax_year_start_index:
                        transaction_amount = self.disposal_list[date_index][
                            symbol
                        ].amount
                        transaction_fees = self.disposal_list[date_index][symbol].fees
                        transaction_disposal_proceeds = (
                            transaction_amount + transaction_fees
                        )
                        transaction_quantity = self.disposal_list[date_index][
                            symbol
                        ].quantity
                        calculated_quantity = Decimal(0)
                        calculated_proceeds = Decimal(0)
                        calculated_gain = Decimal(0)
                        for entry in calculation_entries:
                            calculated_quantity += entry.quantity
                            calculated_proceeds += entry.amount + entry.fees
                            calculated_gain += entry.gain
                        assert transaction_quantity == calculated_quantity
                        assert round_decimal(
                            transaction_disposal_proceeds, 10
                        ) == round_decimal(calculated_proceeds, 10), (
                            f"{transaction_disposal_proceeds} != {calculated_proceeds}"
                        )
                        assert transaction_capital_gain == round_decimal(
                            calculated_gain, 2
                        )
                        prefix = self._disposal_log_prefix(date_index, symbol)
                        calculation_log[date_index][f"{prefix}${symbol}"] = (
                            calculation_entries
                        )
                        if prefix == "exempt":
                            exempt_disposal_count += 1
                            exempt_disposal_proceeds += transaction_disposal_proceeds
                            LOGGER.debug(
                                "EXEMPT DISPOSAL on %s of %s, quantity %s, proceeds £%s",
                                date_index,
                                symbol,
                                transaction_quantity,
                                round_decimal(transaction_disposal_proceeds, 2),
                            )
                        else:
                            disposal_count += 1
                            disposal_proceeds += transaction_disposal_proceeds
                            allowable_costs += (
                                transaction_disposal_proceeds - transaction_capital_gain
                            )
                            LOGGER.debug(
                                "DISPOSAL on %s of %s, quantity %s, capital gain $%s",
                                date_index,
                                symbol,
                                transaction_quantity,
                                round_decimal(transaction_capital_gain, 2),
                            )
                            if transaction_capital_gain > 0:
                                capital_gain += transaction_capital_gain
                            elif prefix == "gift":
                                # A clogged loss: usable only against gains on
                                # disposals to the same connected person while
                                # still connected (TCGA 1992 s18(3)). Kept out of
                                # the loss total and reported as its own.
                                gift_loss += transaction_capital_gain
                            else:
                                capital_loss += transaction_capital_gain

            if date_index in self.rename_list:
                for old, new in self.rename_list[date_index].items():
                    entry = self.process_rename(old, new)
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"rename${old}"] = [entry]

            self._record_transfers_to_spouse(
                date_index,
                tax_year_start_index,
                calculation_log,
                bnb_claimed_before_today,
            )

            self.verify_split_day_closes(date_index)

            # Excess Reported incomes should be reported at the end of the day
            if date_index in self.eris:
                for symbol in self.eris[date_index]:
                    maybe_entry = self.process_eri(symbol, date_index)
                    if not maybe_entry:
                        continue

                    if date_index >= tax_year_start_index:
                        eris = maybe_entry.eris
                        assert eris
                        calculation_log[date_index][
                            f"excess-reported-income${symbol}"
                        ] = [maybe_entry]

            # Lastly all the ERI distribution events
            if date_index in self.eris_distribution:
                for symbol in self.eris_distribution[date_index]:
                    data = self.eris_distribution[date_index][symbol]
                    is_interest = symbol in self.interest_fund_tickers
                    if is_interest:
                        self.total_foreign_interest += data.amount
                    self.calculation_log_yields[date_index][
                        f"excess-reported-income-distribution${symbol}"
                    ] = [
                        CalculationEntry(
                            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                            quantity=data.quantity,
                            amount=data.amount,
                            new_quantity=data.quantity,
                            gain=None,
                            fees=Decimal(0),
                            new_pool_cost=data.amount,
                            allowable_cost=None,
                            eris=[
                                ExcessReportedIncome(
                                    price=data.price,
                                    symbol=symbol,
                                    date=date_index - ERI_TAX_DATE_DELTA,
                                    distribution_date=date_index,
                                    is_interest=is_interest,
                                ),
                            ],
                        )
                    ]

        self.process_dividends()
        self.process_interests()

        LOGGER.info(
            "\n%s\n",
            style_text(
                "Second pass complete", colour=Fore.GREEN, emoji="✅", stream=sys.stderr
            ),
        )
        allowance = CAPITAL_GAIN_ALLOWANCES.get(self.tax_year)
        dividend_allowance = DIVIDEND_ALLOWANCES.get(self.tax_year)

        return CapitalGainsReport(
            self.tax_year,
            [
                self.make_portfolio_entry(symbol, position.quantity, position.amount)
                for symbol, position in self.portfolio.items()
            ],
            disposal_count,
            round_decimal(disposal_proceeds, 2),
            round_decimal(allowable_costs, 2),
            round_decimal(capital_gain, 2),
            round_decimal(capital_loss, 2),
            Decimal(allowance) if allowance is not None else None,
            Decimal(dividend_allowance) if dividend_allowance is not None else None,
            calculation_log,
            dict(sorted(self.calculation_log_yields.items())),
            round_decimal(self.total_uk_interest, 2),
            round_decimal(self.total_foreign_interest, 2),
            round_decimal(self.total_interest_tax, 2),
            show_unrealized_gains=self.calc_unrealized_gains,
            gift_loss=round_decimal(gift_loss, 2),
            period_start=self.period_start,
            period_end=self.period_end,
            exempt_disposal_count=exempt_disposal_count,
            exempt_disposal_proceeds=round_decimal(exempt_disposal_proceeds, 2),
        )

    def make_portfolio_entry(
        self, symbol: str, quantity: Decimal, amount: Decimal
    ) -> PortfolioEntry:
        """Create a portfolio entry in the report."""
        unrealized_gains = None
        if self.calc_unrealized_gains:
            current_price = (
                self.price_fetcher.get_current_market_price(symbol)
                if quantity > 0
                else 0
            )
            if current_price is not None:
                unrealized_gains = current_price * quantity - amount
        return PortfolioEntry(
            symbol,
            quantity,
            amount,
            unrealized_gains,
        )


def calculate_cgt(args: argparse.Namespace) -> None:
    """Perform all the computations."""

    isin_translation_file = args.isin_translation_file

    # Read data from input files
    isin_converter = IsinConverter(isin_translation_file)
    broker_transactions = BrokerRegistry.load_all_transactions(args, isin_converter)
    currency_converter = CurrencyConverter.create(args.exchange_rates_file)
    price_fetcher = CurrentPriceFetcher(currency_converter)
    initial_prices = InitialPrices(args.initial_prices_file)
    spin_off_handler = SpinOffHandler(args.spin_offs_file)

    calculator = CapitalGainsCalculator(
        args.year,
        currency_converter,
        isin_converter,
        price_fetcher,
        spin_off_handler,
        initial_prices,
        args.interest_fund_tickers,
        cgt_exempt_tickers=args.cgt_exempt_tickers,
        balance_check=args.balance_check,
        calc_unrealized_gains=args.calc_unrealized_gains,
        period_start=args.period_from,
        period_end=args.period_to,
    )
    # First pass converts broker transactions to HMRC transactions.
    # This means applying same day rule and collapsing all transactions with
    # same type within the same day.
    # It also converts prices to GBP, validates data and calculates dividends,
    # taxes on dividends and interest.
    calculator.convert_to_hmrc_transactions(broker_transactions)
    # Second pass calculates capital gain tax for the given tax year.
    report = calculator.calculate_capital_gain()
    # The report string is newline-terminated already; avoid a trailing
    # blank line so piped output stays stable under newline normalisation.
    print(report, end="")

    # Generate PDF report.
    if not args.no_report:
        render_latex.render_pdf(
            report,
            output_path=args.output,
            skip_pdflatex=args.no_pdflatex,
        )
    done_msg = (
        "Done! Calculations complete (PDF generation skipped)."
        if args.no_pdflatex or args.no_report
        else "Done! Report generated successfully."
    )
    LOGGER.info(style_text(done_msg, colour=Fore.GREEN, emoji="🎉", stream=sys.stderr))


def main() -> int:
    """Run main function."""

    # Enable colourised logging.
    setup_logging()

    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True

    parser = create_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    reject_duplicate_stdin(parser, args)
    reject_schwab_file_and_dir(parser, args)
    resolve_reporting_period(parser, args)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        calculate_cgt(args)
    except CgtError as err:
        if args.verbose:
            LOGGER.exception("Exception:")
        else:
            # Print error without traceback
            LOGGER.error("%s", err)
        return 1
    except Exception:
        # Last-resort catch for unexpected exceptions
        LOGGER.critical("Unexpected error!")
        LOGGER.exception("Details:")
        return 1

    return 0


def init() -> None:
    """Entry point."""
    sys.exit(main())


if __name__ == "__main__":
    init()
