#!/usr/bin/env python3
"""Capital Gain Calculator main module."""

from __future__ import annotations

from collections import Counter, defaultdict
import datetime
import decimal
from decimal import Decimal
import logging
import sys
from typing import TYPE_CHECKING

from colorama import Fore, Style

from . import render_latex
from .args_parser import create_parser, resolve_reporting_period
from .const import (
    BALANCE_CHECK_CONTEXT_ROWS,
    BED_AND_BREAKFAST_DAYS,
    CAPITAL_GAIN_ALLOWANCES,
    DIVIDEND_ALLOWANCES,
    DIVIDEND_CURRENCY_TO_COUNTRY,
    DIVIDEND_DOUBLE_TAXATION_RULES,
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
from .spin_off_handler import SpinOffHandler
from .transaction_log import add_to_list, has_key
from .util import approx_equal, normalize_amount, round_decimal, strip_zeros

if TYPE_CHECKING:
    import argparse

LOGGER = logging.getLogger(__name__)


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
        self.total_uk_interest = Decimal(0)
        self.total_foreign_interest = Decimal(0)
        self.total_interest_tax = Decimal(0)

        self.acquisition_list: HmrcTransactionLog = {}
        self.disposal_list: HmrcTransactionLog = {}
        # No gain/no loss transfers to a spouse/civil partner. Kept separate from
        # disposal_list so they never enter the taxable disposal totals.
        self.transfer_to_spouse_list: HmrcTransactionLog = {}
        self.bnb_list: HmrcTransactionLog = {}
        self.split_list: dict[tuple[str, datetime.date], Decimal] = {}
        # Shares a split added, by symbol and date. They sit in
        # acquisition_list so they reach the pool, but they cost nothing and
        # are not an acquisition a disposal can be identified against
        # (TCGA 1992 s127), so they have to stay distinguishable from a real
        # purchase made the same day.
        self.split_shares: dict[tuple[str, datetime.date], Decimal] = defaultdict(
            Decimal
        )
        # Stores old->new mapping when a symbol changes its name.
        self.rename_list: dict[datetime.date, dict[str, str]] = defaultdict(dict)

        self.dividend_list: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        self.dividend_tax_list: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
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

    def get_eri(self, symbol: str, date: datetime.date) -> ExcessReportedIncome | None:
        """Return Excess Reported Income at specific date for the input symbol."""
        if date in self.eris and symbol in self.eris[date]:
            return self.eris[date][symbol]
        return None

    def add_acquisition(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Record an acquisition in the portfolio and the acquisition list."""
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
        elif transaction.action is ActionType.STOCK_SPLIT:
            price = Decimal(0)
            amount = Decimal(0)
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
        quantity = get_quantity_or_fail(transaction)

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
        """Record a disposal in the portfolio and the disposal list."""
        symbol = get_symbol_or_fail(transaction)
        quantity = transaction.quantity
        if symbol not in self.portfolio:
            raise InvalidTransactionError(
                transaction, "Tried to sell not owned symbol, reversed order?"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        if self.portfolio[symbol].quantity < quantity:
            raise InvalidTransactionError(
                transaction,
                "Tried to sell more than the available "
                f"balance({self.portfolio[symbol].quantity})",
            )

        amount = get_amount_or_fail(transaction)
        price = transaction.price

        self.portfolio[symbol] -= Position(quantity, amount)

        if self.portfolio[symbol].quantity == 0:
            del self.portfolio[symbol]

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
            quantity,
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
        if symbol not in self.portfolio:
            raise InvalidTransactionError(
                transaction, "Tried to transfer to spouse a not owned symbol"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        if self.portfolio[symbol].quantity < quantity:
            raise InvalidTransactionError(
                transaction,
                "Tried to transfer to spouse more than the available "
                f"balance({self.portfolio[symbol].quantity})",
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
        position = self.portfolio[symbol]
        cost = normalize_amount(position.amount * quantity / position.quantity)
        self.portfolio[symbol] -= Position(quantity, cost)
        if self.portfolio[symbol].quantity == 0:
            del self.portfolio[symbol]
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
        if symbol not in self.portfolio:
            raise InvalidTransactionError(
                transaction, "Tried to give away a not owned symbol"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        if self.portfolio[symbol].quantity < quantity:
            raise InvalidTransactionError(
                transaction,
                "Tried to give away more than the available "
                f"balance({self.portfolio[symbol].quantity})",
            )
        if transaction.price is None:
            raise PriceMissingError(transaction)
        # Zero is a real market value: a holding can become worthless, and
        # giving it away disposes of it for nothing, leaving the cost as a
        # loss. Only a negative value is nonsense.
        if transaction.price < 0:
            raise InvalidTransactionError(
                transaction, "A gift cannot have a negative market value"
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
        position = self.portfolio[symbol]
        cost = normalize_amount(position.amount * quantity / position.quantity)
        self.portfolio[symbol] -= Position(quantity, cost)
        if self.portfolio[symbol].quantity == 0:
            del self.portfolio[symbol]
        # Recorded like a sale: the amount net of fees, the fees alongside, so
        # that proceeds come to the market value and the fees are allowed as
        # a cost of the disposal.
        add_to_list(
            self.disposal_list,
            transaction.date,
            symbol,
            quantity,
            self.currency_converter.to_gbp_for(
                quantity * transaction.price - transaction.fees, transaction
            ),
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )
        self.gift_disposals[transaction.date, symbol] = connected

    def _disposal_log_prefix(self, date_index: datetime.date, symbol: str) -> str:
        """Name the kind of disposal recorded for a symbol on a day.

        A sale with a gift to someone unconnected is one ordinary disposal,
        reported as a sale.
        """
        key = (date_index, symbol)
        connected = self.gift_disposals.get(key)
        if connected:
            return "gift"
        if connected is None or key in self.sale_days:
            return "sell"
        return "gift-unconnected"

    def add_shares_given_away(self, transaction: BrokerTransaction) -> None:
        """Record shares handed to someone for nothing.

        To a spouse that is no gain/no loss; to anyone else it is a disposal
        at market value.
        """
        if transaction.action is ActionType.TRANSFER_TO_SPOUSE:
            self.add_transfer_to_spouse(transaction)
        else:
            self.add_gift(transaction)

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
        self.rename_list[transaction.date][old_symbol] = new_symbol
        self.portfolio[new_symbol] += self.portfolio.pop(old_symbol, Position())

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

        for i, transaction in enumerate(transactions):
            self._convert_foreign_fees(transaction)
            if transaction.action == ActionType.EXCESS_REPORTED_INCOME:
                self.add_eri(transaction)
                balance_history.append(
                    Decimal(0)
                )  # dummy value, this will get filtered out
                continue
            new_balance = balance[transaction.broker, transaction.currency]
            if transaction.action is ActionType.TRANSFER:
                new_balance += get_amount_or_fail(transaction)
            elif transaction.action in {
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
            elif transaction.action == ActionType.STOCK_SPLIT:
                # Calculate the multiplier based on portfolio and received shares
                acquired_quantity = get_quantity_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                holding_quantity = self.portfolio[symbol].quantity
                multiplier = (acquired_quantity + holding_quantity) / holding_quantity
                self.split_list[symbol, transaction.date] = multiplier
                self.split_shares[symbol, transaction.date] += acquired_quantity
                self.add_acquisition(transaction)
            elif transaction.action in {ActionType.DIVIDEND, ActionType.CAPITAL_GAIN}:
                amount = get_amount_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                self.dividend_list[symbol, transaction.date] += ForeignCurrencyAmount(
                    amount, currency
                )
                if self.date_in_tax_year(transaction.date):
                    dividends[symbol, currency] += amount
            elif transaction.action is ActionType.DIVIDEND_TAX:
                amount = get_amount_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                self.dividend_tax_list[symbol, transaction.date] += (
                    ForeignCurrencyAmount(amount, currency)
                )
                if self.date_in_tax_year(transaction.date):
                    dividends_tax[symbol, currency] += amount
            elif transaction.action is ActionType.ADJUSTMENT:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
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
            elif transaction.action is ActionType.WIRE_FUNDS_RECEIVED:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
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
                self.add_shares_given_away(transaction)
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
        if dividends or dividends_tax:
            print()
            print(style_text("Dividends", colour=Style.BRIGHT, emoji="💵"))
            # Withholding can arrive without income in the same run, so taxed
            # symbols are listed even when no dividend was paid.
            keys = list(dividends)
            keys += [key for key in dividends_tax if key not in dividends]
            for symbol, currency in keys:
                amount = dividends.get((symbol, currency), Decimal(0))
                tax = round_decimal(
                    -dividends_tax.get((symbol, currency), Decimal(0)), 2
                )
                tax_str = f", excluding {tax} taxed at source" if tax > 0 else ""
                print(
                    f"{bul}{symbol}: {round_decimal(amount, 2)}{tax_str} ({currency})"
                )
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

            split_multiplier = Decimal(1)
            effective_symbol = symbol

            for i in range(BED_AND_BREAKFAST_DAYS):
                search_index = date_index + datetime.timedelta(days=i + 1)
                # HMRC treats renames as the same security for B&B purposes.
                effective_symbol = self.rename_list.get(search_index, {}).get(
                    effective_symbol, effective_symbol
                )

                # Check if there was any stock split, in which case we need to adjust the B&B quantity
                split_multiplier *= self.split_list.get(
                    (effective_symbol, search_index), Decimal(1)
                )

                # ERI is distributed annually, but when a fund closes we might have
                # multiple ERI distributions in close succession
                eri = self.get_eri(effective_symbol, search_index)
                if eri:
                    eris.append(eri)
                # Shares from a split are free, so they are never part of a
                # B&B match and _matchable_acquisition leaves them out. Say so:
                # a split this close to a disposal is worth a second look.
                if self.split_shares.get((effective_symbol, search_index)):
                    LOGGER.warning(
                        "A split happened shortly after a disposal of %s, double check these transactions."
                        "Disposed on %s and split happened on %s",
                        symbol,
                        date_index,
                        search_index,
                    )
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
                    if split_multiplier != Decimal(1):
                        LOGGER.warning(
                            "Bed & breakfast for %s is taking into account a {%.2f}x split "
                            "that happened shortly before the repurchase of shares",
                            symbol,
                            split_multiplier,
                        )
                    # Everything reserved here is counted in the acquisition's
                    # own units, so the whole remainder converts to the
                    # disposal's units once. Converting only the acquisition
                    # would subtract post-split counts from a pre-split one and
                    # can go negative.
                    available_quantity = min(
                        disposal_quantity,
                        (
                            acquisition.quantity
                            - same_day_disposal.quantity
                            - bnb_acquisition.quantity
                        )
                        / split_multiplier,
                    )
                    fees = (
                        disposal.fees * available_quantity / original_disposal_quantity
                    )
                    adjusted_acquisition_quantity = (
                        acquisition.quantity / split_multiplier
                    )
                    # Multiply by available_quantity before divide to avoid rounding errors from division
                    bnb_acquisition_cost = normalize_amount(
                        (available_quantity * acquisition.amount)
                        / adjusted_acquisition_quantity
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
                        available_quantity * split_multiplier,
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

    def _matchable_acquisition(
        self,
        date_index: datetime.date,
        symbol: str,
    ) -> HmrcTransactionData:
        """Return the acquisitions a disposal could be identified with.

        Shares handed over by a split are removed: they cost nothing and are
        not an acquisition (TCGA 1992 s127, CG51805). They are pooled with real
        purchases in ``acquisition_list``, so leaving them in would spread the
        purchase cost over free shares as well.
        """
        if not has_key(self.acquisition_list, date_index, symbol):
            return HmrcTransactionData()
        acquisition = self.acquisition_list[date_index][symbol]
        split = self.split_shares.get((symbol, date_index), Decimal(0))
        if not split:
            return acquisition
        return HmrcTransactionData(
            quantity=acquisition.quantity - split,
            amount=acquisition.amount,
            fees=acquisition.fees,
            eris=acquisition.eris,
        )

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

    def process_dividends(self) -> None:
        """Process all dividend events and taxes.

        It updates the interest total for the year if needed.
        """
        for (symbol, date), foreign_amount in self.dividend_list.items():
            # Dividends outside the computed tax year are not reported, so
            # neither are the warnings raised while resolving their treaty.
            if not self.date_in_tax_year(date):
                continue

            tax = self.dividend_tax_list[symbol, date]
            currency = foreign_amount.currency
            assert currency is not None, f"Dividend for {symbol} has no currency"

            treaty = None
            is_interest_fund = symbol in self.interest_fund_tickers
            if tax.amount < 0:
                if is_interest_fund:
                    LOGGER.warning(
                        "Cannot apply taxation treaty for bond fund %s", symbol
                    )
                else:
                    if tax.currency != foreign_amount.currency:
                        raise CalculationError(
                            f"Dividend and withholding tax currencies do not match "
                            f"for {symbol} on {date}: dividend "
                            f"{foreign_amount.currency}, tax {tax.currency}"
                        )
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
        begin_index = INTERNAL_START_DATE
        tax_year_start_index = self.tax_year_start_date
        end_index = self.tax_year_end_date
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        gift_loss = Decimal(0)

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
            if date_index in self.disposal_list:
                for symbol in self.disposal_list[date_index]:
                    transaction_capital_gain, calculation_entries = (
                        self.process_disposal(symbol, date_index)
                    )
                    if date_index >= tax_year_start_index:
                        disposal_count += 1
                        transaction_amount = self.disposal_list[date_index][
                            symbol
                        ].amount
                        transaction_fees = self.disposal_list[date_index][symbol].fees
                        transaction_disposal_proceeds = (
                            transaction_amount + transaction_fees
                        )
                        disposal_proceeds += transaction_disposal_proceeds
                        allowable_costs += (
                            transaction_disposal_proceeds - transaction_capital_gain
                        )
                        transaction_quantity = self.disposal_list[date_index][
                            symbol
                        ].quantity
                        LOGGER.debug(
                            "DISPOSAL on %s of %s, quantity %s, capital gain $%s",
                            date_index,
                            symbol,
                            transaction_quantity,
                            round_decimal(transaction_capital_gain, 2),
                        )
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
        if args.no_pdflatex
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
