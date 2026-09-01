"""First pass: turning broker transactions into HMRC transactions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import logging
import sys
from typing import TYPE_CHECKING

from colorama import Fore, Style

from .const import (
    BALANCE_CHECK_CONTEXT_ROWS,
    ERI_TAX_DATE_DELTA,
    RENAME_DESCRIPTION_PREFIX,
)
from .exceptions import (
    AmountMissingError,
    CalculatedAmountDiscrepancyError,
    CalculationError,
    InvalidTransactionError,
    IsinMissingError,
    PriceMissingError,
    QuantityMissingError,
    QuantityNotPositiveError,
    SymbolMissingError,
    UnclassifiedGiftError,
)
from .logging import bullet, style_text
from .model import (
    ActionType,
    BrokerTransaction,
    CalculationType,
    CurrencyCode,
    ExcessReportedIncome,
    ForeignCurrencyAmount,
    OptionDisposalData,
    Position,
    SpinOff,
)
from .stock_split_planning import plan_stock_splits, source_account
from .stock_splits import quantity_sign
from .transaction_log import add_to_list
from .util import approx_equal, normalize_amount, round_decimal

if TYPE_CHECKING:
    from collections.abc import Callable
    import datetime

    from .calculator_state import CalculatorState
    from .currency_converter import CurrencyConverter
    from .current_price_fetcher import CurrentPriceFetcher
    from .income import IncomeProcessor
    from .initial_prices import InitialPrices
    from .isin_converter import IsinConverter
    from .spin_off_handler import SpinOffHandler

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


@dataclass(frozen=True)
class FirstPassTotals:
    """Running totals the first pass reports at the end.

    `dividends` and `dividends_tax` are keyed by (symbol, currency); the
    rest by (broker, currency).
    """

    balance: dict[tuple[str, CurrencyCode], Decimal]
    dividends: dict[tuple[str, CurrencyCode], Decimal]
    dividends_tax: dict[tuple[str, CurrencyCode], Decimal]
    interests: dict[tuple[str, CurrencyCode], Decimal]
    interest_taxes: dict[tuple[str, CurrencyCode], Decimal]


class TransactionIngester:
    """Records broker transactions as HMRC acquisitions, disposals and income."""

    def __init__(
        self,
        state: CalculatorState,
        income: IncomeProcessor,
        currency_converter: CurrencyConverter,
        isin_converter: IsinConverter,
        price_fetcher: CurrentPriceFetcher,
        spin_off_handler: SpinOffHandler,
        initial_prices: InitialPrices,
        interest_fund_tickers: list[str],
        *,
        balance_check: bool,
        autoconvert_currency: bool = False,
        date_in_tax_year: Callable[[datetime.date], bool],
    ):
        """Create transaction ingester object."""
        self.state = state
        self.income = income
        self.currency_converter = currency_converter
        self.isin_converter = isin_converter
        self.price_fetcher = price_fetcher
        self.spin_off_handler = spin_off_handler
        self.initial_prices = initial_prices
        self.interest_fund_tickers = interest_fund_tickers
        self.balance_check = balance_check
        self.autoconvert_currency = autoconvert_currency
        self.date_in_tax_year = date_in_tax_year

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

        capital_adjustment, capital_fee_adjustment = self._capital_adjustments_gbp(
            transaction
        )
        source_adjustment = self._capital_adjustments_in_transaction_currency(
            transaction
        )
        quantity = self._pooled_quantity(transaction)
        self.state.portfolio[symbol] += Position(quantity, amount - source_adjustment)

        gbp_amount = (
            self.currency_converter.to_gbp_for(amount, transaction) - capital_adjustment
        )
        if transaction.action is ActionType.SPIN_OFF:
            self.state.spin_off_estimates[transaction.date, symbol] += gbp_amount
        add_to_list(
            self.state.acquisition_list,
            transaction.date,
            symbol,
            quantity,
            gbp_amount,
            self.currency_converter.to_gbp_for(transaction.fees, transaction)
            + capital_fee_adjustment,
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
        capacity = self.state.split_day_capacity.get((symbol, date_index))
        if capacity is not None:
            return capacity
        if symbol not in self.state.portfolio:
            return None
        return self.state.portfolio[symbol].quantity

    def _provisional_cost(self, symbol: str, quantity: Decimal) -> Decimal:
        """Cost to take out of the first-pass pool for shares leaving it.

        Only bookkeeping, so that later same-symbol rows validate; the second
        pass works the real figure out from the rebuilt pool. On a
        reorganisation day the running count can sit at or below zero here,
        and there is then nothing to apportion.
        """
        position = self.state.portfolio[symbol]
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
        if self.state.portfolio[symbol].quantity == 0:
            del self.state.portfolio[symbol]

    def _pooled_quantity(self, transaction: BrokerTransaction) -> Decimal:
        """Return the count to pool, in the units in force at the day's end."""
        quantity = transaction.pool_quantity
        if quantity is None:
            raise QuantityMissingError(transaction)
        return quantity

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
            symbol, transaction.date, self.state.portfolio
        )
        # Nothing to apportion from an empty holding, and no proportion of
        # value to work out either. On a day the source is itself created by
        # a spin-off, this is the first sign the rows are out of order, and
        # it comes before any price is asked for.
        if self.state.portfolio[ticker].quantity == 0:
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
                for spin_off in self.state.spin_offs[transaction.date]
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
        src_amount = self.state.portfolio[ticker].quantity * src_price
        original_src_amount = self.state.portfolio[ticker].amount

        share_of_original_cost = src_amount / (dst_amount + src_amount)
        self.state.spin_offs[transaction.date].append(
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

        self.state.portfolio[symbol] -= Position(pooled_quantity, amount)

        self._drop_empty_holding(symbol)

        if price is None:
            raise PriceMissingError(transaction)
        # A gift to a connected person on the same day is refused (see
        # add_gift); a gift to anyone else folds into this ordinary disposal.
        if self.state.gift_disposals.get((transaction.date, symbol)):
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
        capital_adjustment, capital_fee_adjustment = self._capital_adjustments_gbp(
            transaction
        )
        add_to_list(
            self.state.disposal_list,
            transaction.date,
            symbol,
            pooled_quantity,
            self.currency_converter.to_gbp_for(amount, transaction)
            + capital_adjustment,
            self.currency_converter.to_gbp_for(transaction.fees, transaction)
            + capital_fee_adjustment,
        )
        self.state.sale_days.add((transaction.date, symbol))

    def _capital_adjustments_gbp(
        self, transaction: BrokerTransaction
    ) -> tuple[Decimal, Decimal]:
        """Convert assigned-option adjustments at each premium's own date."""
        amount = Decimal(0)
        fees = Decimal(0)
        for adjustment in transaction.capital_adjustments:
            amount += self.currency_converter.to_gbp(
                adjustment.net_amount,
                adjustment.currency,
                adjustment.date,
            )
            fees += self.currency_converter.to_gbp(
                adjustment.fees,
                adjustment.currency,
                adjustment.date,
            )
        return amount, fees

    @staticmethod
    def _capital_adjustments_in_transaction_currency(
        transaction: BrokerTransaction,
    ) -> Decimal:
        """Return adjustments for first-pass bookkeeping in broker currency."""
        if any(
            adjustment.currency != transaction.currency
            for adjustment in transaction.capital_adjustments
        ):
            raise InvalidTransactionError(
                transaction,
                "Assigned option and underlying share use different currencies",
            )
        return sum(
            (adjustment.net_amount for adjustment in transaction.capital_adjustments),
            Decimal(0),
        )

    def add_written_option_grant(self, transaction: BrokerTransaction) -> None:
        """Record the taxable part of a written option grant."""
        contract = transaction.option_contract
        tax_data = transaction.written_option_tax
        quantity = get_quantity_or_fail(transaction)
        amount = get_amount_or_fail(transaction)
        if contract is None or tax_data is None:
            raise InvalidTransactionError(
                transaction, "Written option is missing its reconciled tax details"
            )
        if quantity <= 0 or tax_data.taxable_quantity < 0:
            raise QuantityNotPositiveError(transaction)
        if tax_data.taxable_quantity == 0:
            return

        proportion = tax_data.taxable_quantity / quantity
        proceeds = self.currency_converter.to_gbp_for(
            (amount + transaction.fees) * proportion,
            transaction,
        )
        allowable_cost = self.currency_converter.to_gbp_for(
            transaction.fees * proportion,
            transaction,
        )
        allowable_cost += sum(
            (
                self.currency_converter.to_gbp(
                    cost.amount,
                    cost.currency,
                    cost.date,
                )
                for cost in tax_data.close_costs
            ),
            Decimal(0),
        )
        option_name = str(contract)
        current = self.state.option_disposal_list[transaction.date].setdefault(
            option_name, OptionDisposalData()
        )
        self.state.option_disposal_list[transaction.date][option_name] = (
            current
            + OptionDisposalData(
                quantity=tax_data.taxable_quantity,
                proceeds=proceeds,
                allowable_cost=allowable_cost,
            )
        )

    def process_written_option_transaction(
        self, transaction: BrokerTransaction
    ) -> Decimal:
        """Record a written-option row and return its cash movement."""
        amount = get_amount_or_fail(transaction)
        if transaction.action is ActionType.OPTION_GRANT:
            self.add_written_option_grant(transaction)
        return amount

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
        self.state.portfolio[symbol] -= Position(
            quantity, self._provisional_cost(symbol, quantity)
        )
        self._drop_empty_holding(symbol)
        add_to_list(
            self.state.transfer_to_spouse_list,
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
        if connected and key in self.state.sale_days:
            raise CalculationError(
                self._sale_and_gift_message(symbol, transaction.date)
            )
        if self.state.gift_disposals.get(key, connected) != connected:
            raise CalculationError(self._mixed_gifts_message(symbol, transaction.date))
        # Connected gifts must also agree on the value per unit. One holding
        # of one class has one market value on a day, so a second value means
        # separately valued gifts to different recipients (CG59562), and the
        # day's gain or loss could not be split between them for s18(3).
        # Everything at one value is treated as one gift to one person.
        if connected:
            value = (transaction.price, transaction.currency)
            if self.state.gift_prices.setdefault(key, value) != value:
                raise CalculationError(
                    self._gift_values_differ_message(symbol, transaction.date)
                )
        # Reduce the first-pass holding so later same-symbol transactions
        # validate; the pool cost is recomputed in the second pass.
        self.state.portfolio[symbol] -= Position(
            quantity, self._provisional_cost(symbol, quantity)
        )
        self._drop_empty_holding(symbol)
        # Recorded like a sale: the amount net of fees, the fees alongside, so
        # that proceeds come to the market value and the fees are allowed as
        # a cost of the disposal.
        add_to_list(
            self.state.disposal_list,
            transaction.date,
            symbol,
            quantity,
            self.currency_converter.to_gbp_for(
                market_value - transaction.fees, transaction
            ),
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )
        self.state.gift_disposals[transaction.date, symbol] = connected

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
        https://www.gov.uk/government/publications/approved-offshore-reporting-funds
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

            for report_date, report_by_symbol in self.state.eris.items():
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

            self.state.eris[transaction.date][symbol] = ExcessReportedIncome(
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
        existing = self.state.rename_list[transaction.date].get(old_symbol)
        if existing is not None and existing != new_symbol:
            raise CalculationError(
                self._two_renames_message(
                    old_symbol, transaction.date, existing, new_symbol
                )
            )
        self.state.rename_list[transaction.date][old_symbol] = new_symbol
        position = self.state.portfolio.pop(old_symbol, Position())
        self.state.portfolio[new_symbol] += position
        # The units keep the accounts that put them there; only the name
        # changes. Nothing is forgotten here, whatever the count says. The
        # merged stream's order within a day is not chronology, so a count of
        # zero at this row is not the old name emptying, and a rename is no
        # better placed to read it than the disposal that took it there. That
        # question is settled where every earlier day has closed, in
        # ``_open_transaction_day``, which is the only place a holding's
        # accounts are dropped.
        moved = self.state.holding_sources.pop(old_symbol, set())
        if moved:
            self.state.holding_sources[new_symbol] |= moved

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
        self.state.fee_days.add((transaction.date, symbol))
        add_to_list(
            self.state.acquisition_list,
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
                for held in self.state.holding_sources
                if held not in self.state.portfolio
                or self.state.portfolio[held].quantity == 0
            ]:
                del self.state.holding_sources[symbol]
            plan_stock_splits(self.state, transaction.date, days[transaction.date])
        if quantity_sign(transaction.action) > 0 and transaction.symbol:
            self.state.holding_sources[transaction.symbol].add(
                source_account(transaction)
            )

    def _record_interest(
        self,
        transaction: BrokerTransaction,
        interests: dict[tuple[str, CurrencyCode], Decimal],
        interest_taxes: dict[tuple[str, CurrencyCode], Decimal],
    ) -> Decimal:
        """Record interest, or tax withheld from it, and return its cash move."""
        amount = get_amount_or_fail(transaction)
        key = (transaction.broker, transaction.currency, transaction.date)
        if transaction.action is ActionType.INTEREST:
            log, year_totals = self.state.interest_list, interests
        else:
            log, year_totals = self.state.interest_tax_list, interest_taxes
        log[key] += ForeignCurrencyAmount(amount, transaction.currency)
        if self.date_in_tax_year(transaction.date):
            year_totals[transaction.broker, transaction.currency] += amount
        return amount

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
            elif transaction.action in {
                ActionType.OPTION_GRANT,
                ActionType.OPTION_CLOSE,
            }:
                # TCGA 1992 s148 makes this an allowable cost of the original
                # grant. The Schwab parser has already attached that cost to
                # the grant; the closing row only moves the cash balance.
                new_balance += self.process_written_option_transaction(transaction)
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
                self.state.dividend_list[symbol, transaction.date] = (
                    self.currency_converter.combine_amounts(
                        self.state.dividend_list[symbol, transaction.date],
                        ForeignCurrencyAmount(amount, currency),
                        transaction.date,
                        autoconvert=self.autoconvert_currency,
                    )
                )
                self.state.dividend_dates[transaction.broker, symbol].add(
                    transaction.date
                )
                if self.date_in_tax_year(transaction.date):
                    dividends[symbol, currency] += amount
            elif transaction.action is ActionType.DIVIDEND_TAX:
                amount = get_amount_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                tax_key = (transaction.broker, symbol, transaction.date, currency)
                self.state.dividend_tax_list[tax_key] += ForeignCurrencyAmount(
                    amount, currency
                )
            # Cash moves and nothing else: a deposit or withdrawal, a
            # correction, or an incoming wire. No shares change hands.
            elif transaction.action in {
                ActionType.ADJUSTMENT,
                ActionType.TRANSFER,
                ActionType.WIRE_FUNDS_RECEIVED,
            }:
                new_balance += get_amount_or_fail(transaction)
            elif transaction.action in {
                ActionType.INTEREST,
                ActionType.INTEREST_TAX,
            }:
                new_balance += self._record_interest(
                    transaction, interests, interest_taxes
                )
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
        attribution = self.income.dividend_tax_attribution()
        for (symbol, date), tax in [
            *attribution.matched.items(),
            *attribution.unmatched.items(),
        ]:
            if self.date_in_tax_year(date) and tax.currency is not None:
                dividends_tax[symbol, tax.currency] += tax.amount

        self.first_pass_report(
            FirstPassTotals(
                balance=balance,
                dividends=dividends,
                dividends_tax=dividends_tax,
                interests=interests,
                interest_taxes=interest_taxes,
            )
        )

    def first_pass_report(self, totals: FirstPassTotals) -> None:
        """Print the results of the first pass."""
        LOGGER.info(
            "\n%s\n",
            style_text(
                "First pass complete", colour=Fore.GREEN, emoji="✅", stream=sys.stderr
            ),
        )
        bul = bullet(sys.stdout)
        print(style_text("Final portfolio", colour=Style.BRIGHT, emoji="📊"))
        if not self.state.portfolio:
            print(f"{bul}(none)")
        for stock, position in sorted(self.state.portfolio.items()):
            print(f"{bul}{stock}: {position}")
        print()
        print(style_text("Final balance", colour=Style.BRIGHT, emoji="💰"))
        for (broker, currency), amount in totals.balance.items():
            print(f"{bul}{broker}: {round_decimal(amount, 2)} ({currency})")
        # Withholding can arrive without income in the same run, so taxed
        # symbols are listed even when no dividend was paid. A tax-only
        # entry whose withholding rounds to 0.00, or is a net refund, has
        # nothing to display, so it is left out entirely.
        keys = list(totals.dividends)
        keys += [key for key in totals.dividends_tax if key not in totals.dividends]
        dividend_lines = []
        for symbol, currency in keys:
            amount = totals.dividends.get((symbol, currency), Decimal(0))
            tax = round_decimal(
                -totals.dividends_tax.get((symbol, currency), Decimal(0)), 2
            )
            if (symbol, currency) not in totals.dividends and tax <= 0:
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
        if totals.interests:
            print()
            print(style_text("Interest", colour=Style.BRIGHT, emoji="🏦"))
            for (broker, currency), amount in totals.interests.items():
                print(f"{bul}{broker}: {round_decimal(amount, 2)} ({currency})")
        if totals.interest_taxes:
            print()
            print(style_text("Interest taxes", colour=Style.BRIGHT, emoji="🧾"))
            for (broker, currency), amount in totals.interest_taxes.items():
                print(f"{bul}{broker}: {round_decimal(-amount, 2)} ({currency})")
        print()

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
