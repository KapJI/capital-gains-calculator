#!/usr/bin/env python3
"""Capital Gain Calculator main module."""
from __future__ import annotations

from collections import defaultdict
import datetime
import decimal
from decimal import Decimal
import importlib.metadata
import logging
from pathlib import Path
import sys

from . import render_latex
from .args_parser import create_parser
from .const import BED_AND_BREAKFAST_DAYS, CAPITAL_GAIN_ALLOWANCES, INTERNAL_START_DATE
from .currency_converter import CurrencyConverter
from .current_price_fetcher import CurrentPriceFetcher
from .dates import get_tax_year_end, get_tax_year_start, is_date
from .exceptions import (
    AmountMissingError,
    CalculatedAmountDiscrepancyError,
    CalculationError,
    InvalidTransactionError,
    PriceMissingError,
    QuantityNotPositiveError,
    SymbolMissingError,
)
from .initial_prices import InitialPrices
from .model import (
    ActionType,
    BrokerTransaction,
    CalculationEntry,
    CalculationLog,
    CapitalGainsReport,
    HmrcTransactionData,
    HmrcTransactionLog,
    PortfolioEntry,
    Position,
    RuleType,
    SpinOff,
)
from .parsers import read_broker_transactions, read_initial_prices
from .transaction_log import add_to_list, has_key
from .util import round_decimal

LOGGER = logging.getLogger(__name__)


def get_amount_or_fail(transaction: BrokerTransaction) -> Decimal:
    """Return the transaction amount or throw an error."""
    amount = transaction.amount
    if amount is None:
        raise AmountMissingError(transaction)
    return amount


# It is not clear how Schwab or other brokers round the dollar value,
# so assume the values are equal if they are within $0.01.
def _approx_equal(val_a: Decimal, val_b: Decimal) -> bool:
    return abs(val_a - val_b) < Decimal("0.01")


class CapitalGainsCalculator:
    """Main calculator class."""

    def __init__(
        self,
        tax_year: int,
        converter: CurrencyConverter,
        price_fetcher: CurrentPriceFetcher,
        initial_prices: InitialPrices,
        balance_check: bool = True,
        calc_unrealized_gains: bool = False,
    ):
        """Create calculator object."""
        self.tax_year = tax_year

        self.tax_year_start_date = get_tax_year_start(tax_year)
        self.tax_year_end_date = get_tax_year_end(tax_year)

        self.converter = converter
        self.price_fetcher = price_fetcher
        self.initial_prices = initial_prices
        self.balance_check = balance_check
        self.calc_unrealized_gains = calc_unrealized_gains

        self.acquisition_list: HmrcTransactionLog = {}
        self.disposal_list: HmrcTransactionLog = {}
        self.bnb_list: HmrcTransactionLog = {}

        self.portfolio: dict[str, Position] = defaultdict(Position)

    def date_in_tax_year(self, date: datetime.date) -> bool:
        """Check if date is within current tax year."""
        assert is_date(date)
        return self.tax_year_start_date <= date <= self.tax_year_end_date

    def add_acquisition(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Add new acquisition to the given list."""
        symbol = transaction.symbol
        quantity = transaction.quantity
        price = transaction.price
        spin_off = None

        if symbol is None:
            raise SymbolMissingError(transaction)
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)

        # Add to acquisition_list to apply same day rule
        if transaction.action is ActionType.STOCK_ACTIVITY:
            if price is None:
                price = self.initial_prices.get(transaction.date, symbol)
            amount = round_decimal(quantity * price, 2)
        elif transaction.action is ActionType.SPIN_OFF:
            price, amount, spin_off = self.handle_spin_off(transaction)
        elif transaction.action is ActionType.STOCK_SPLIT:
            price = Decimal(0)
            amount = Decimal(0)
        else:
            if price is None:
                raise PriceMissingError(transaction)

            amount = get_amount_or_fail(transaction)
            calculated_amount = quantity * price + transaction.fees
            if not _approx_equal(amount, -calculated_amount):
                raise CalculatedAmountDiscrepancyError(transaction, -calculated_amount)
            amount = -amount

        self.portfolio[symbol] += Position(quantity, amount)

        add_to_list(
            self.acquisition_list,
            transaction.date,
            symbol,
            quantity,
            self.converter.to_gbp_for(amount, transaction),
            self.converter.to_gbp_for(transaction.fees, transaction),
            spin_off,
        )

    def handle_spin_off(
        self,
        transaction: BrokerTransaction,
    ) -> tuple[Decimal, Decimal, SpinOff]:
        """
        Handle spin off transaction.

        Doc basing on SOLV spin off out of MMM.

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
        symbol = transaction.symbol
        quantity = transaction.quantity
        assert symbol is not None
        assert quantity is not None

        price = self.price_fetcher.get_closing_price(symbol, transaction.date)
        amount = quantity * price

        while True:
            # This would ideally be fetched from some stock DB but yfinance does not
            # provide any info on SpinOffs
            ticker = input(
                "For a spin off, please enter the original ticker from which the new "
                f"stock (symbol: {symbol}) was spinned off on {transaction.date}: "
            )
            if ticker in self.portfolio:
                break
            print(f"Invalid ticker: {ticker}, couldn't find it in the portfolio!")
            if len(self.portfolio) <= 10:
                print(f"Available choices: {sorted(self.portfolio)}")

        original_ticket_amount = self.portfolio[ticker].amount
        share_of_original_cost = original_ticket_amount / (
            amount + original_ticket_amount
        )

        for transactions in self.acquisition_list.values():
            if old_transaction := transactions.get(ticker):
                new_amount = share_of_original_cost * old_transaction.amount
                old_transaction.spin_off = SpinOff(
                    dest=symbol,
                    source=ticker,
                    amount=old_transaction.amount,
                    date=transaction.date,
                )
                old_transaction.amount = new_amount
        spin_off = SpinOff(
            amount=amount,
            dest=symbol,
            source=ticker,
            date=transaction.date,
        )
        amount = (1 - share_of_original_cost) * original_ticket_amount
        return price, round_decimal(amount, 2), spin_off

    def add_disposal(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Add new disposal to the given list."""
        symbol = transaction.symbol
        quantity = transaction.quantity
        if symbol is None:
            raise SymbolMissingError(transaction)
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
        calculated_amount = quantity * price - transaction.fees
        if not _approx_equal(amount, calculated_amount):
            raise CalculatedAmountDiscrepancyError(transaction, calculated_amount)
        add_to_list(
            self.disposal_list,
            transaction.date,
            symbol,
            quantity,
            self.converter.to_gbp_for(amount, transaction),
            self.converter.to_gbp_for(transaction.fees, transaction),
        )

    def convert_to_hmrc_transactions(
        self,
        transactions: list[BrokerTransaction],
    ) -> None:
        """Convert broker transactions to HMRC transactions."""
        # We keep a balance per broker,currency pair
        balance: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        dividends = Decimal(0)
        dividends_tax = Decimal(0)
        interest = Decimal(0)
        total_sells = Decimal(0)

        for i, transaction in enumerate(transactions):
            new_balance = balance[(transaction.broker, transaction.currency)]
            if transaction.action is ActionType.TRANSFER:
                new_balance += get_amount_or_fail(transaction)
            elif transaction.action in [
                ActionType.BUY,
                ActionType.REINVEST_SHARES,
            ]:
                new_balance += get_amount_or_fail(transaction)
                self.add_acquisition(transaction)
            elif transaction.action is ActionType.SELL:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                self.add_disposal(transaction)
                if self.date_in_tax_year(transaction.date):
                    total_sells += self.converter.to_gbp_for(amount, transaction)
            elif transaction.action is ActionType.FEE:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                transaction.fees = -amount
                transaction.quantity = Decimal(0)
                gbp_fees = self.converter.to_gbp_for(transaction.fees, transaction)
                if transaction.symbol is None:
                    raise SymbolMissingError(transaction)
                add_to_list(
                    self.acquisition_list,
                    transaction.date,
                    transaction.symbol,
                    transaction.quantity,
                    gbp_fees,
                    gbp_fees,
                )
            elif transaction.action in [
                ActionType.STOCK_ACTIVITY,
                ActionType.SPIN_OFF,
                ActionType.STOCK_SPLIT,
            ]:
                self.add_acquisition(transaction)
            elif transaction.action in [ActionType.DIVIDEND, ActionType.CAPITAL_GAIN]:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                if self.date_in_tax_year(transaction.date):
                    dividends += self.converter.to_gbp_for(amount, transaction)
            elif transaction.action in [ActionType.TAX, ActionType.ADJUSTMENT]:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                if self.date_in_tax_year(transaction.date):
                    dividends_tax += self.converter.to_gbp_for(amount, transaction)
            elif transaction.action is ActionType.INTEREST:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                if self.date_in_tax_year(transaction.date):
                    interest += self.converter.to_gbp_for(amount, transaction)
            elif transaction.action is ActionType.WIRE_FUNDS_RECEIVED:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
            elif transaction.action is ActionType.REINVEST_DIVIDENDS:
                print(f"WARNING: Ignoring unsupported action: {transaction.action}")
            else:
                raise InvalidTransactionError(
                    transaction, f"Action not processed({transaction.action})"
                )
            if self.balance_check and new_balance < 0:
                msg = f"Reached a negative balance({new_balance})"
                msg += f" for broker {transaction.broker} ({transaction.currency})"
                msg += " after processing the following transactions:\n"
                msg += "\n".join(map(str, transactions[: i + 1]))
                raise CalculationError(msg)
            balance[(transaction.broker, transaction.currency)] = new_balance
        print("First pass completed")
        print("Final portfolio:")
        for stock, position in self.portfolio.items():
            print(f"  {stock}: {position}")
        print("Final balance:")
        for (broker, currency), amount in balance.items():
            print(f"  {broker}: {round_decimal(amount, 2)} ({currency})")
        print(f"Dividends: £{round_decimal(dividends, 2)}")
        print(f"Dividend taxes: £{round_decimal(-dividends_tax, 2)}")
        print(f"Interest: £{round_decimal(interest, 2)}")
        print(f"Disposal proceeds: £{round_decimal(total_sells, 2)}")
        print("")

    def process_acquisition(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> list[CalculationEntry]:
        """Process single acquisition."""
        acquisition = self.acquisition_list[date_index][symbol]
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
            acquisition_price = acquisition.amount / acquisition.quantity
            bnb_acquisition = self.bnb_list[date_index][symbol]
            assert bnb_acquisition.quantity <= acquisition.quantity
            modified_amount -= bnb_acquisition.quantity * acquisition_price
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
                    spin_off=acquisition.spin_off,
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
                    spin_off=acquisition.spin_off,
                )
            )
        return calculation_entries

    def process_disposal(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> tuple[Decimal, list[CalculationEntry]]:
        """Process single disposal."""
        disposal = self.disposal_list[date_index][symbol]
        disposal_quantity = disposal.quantity
        proceeds_amount = disposal.amount
        original_disposal_quantity = disposal_quantity
        disposal_price = proceeds_amount / disposal_quantity
        current_quantity = self.portfolio[symbol].quantity
        current_amount = self.portfolio[symbol].amount
        assert disposal_quantity <= current_quantity
        chargeable_gain = Decimal(0)
        calculation_entries = []
        # Same day rule is first
        if has_key(self.acquisition_list, date_index, symbol):
            same_day_acquisition = self.acquisition_list[date_index][symbol]

            available_quantity = min(disposal_quantity, same_day_acquisition.quantity)
            if available_quantity > 0:
                acquisition_price = (
                    same_day_acquisition.amount / same_day_acquisition.quantity
                )
                same_day_proceeds = available_quantity * disposal_price
                same_day_allowable_cost = available_quantity * acquisition_price
                same_day_gain = same_day_proceeds - same_day_allowable_cost
                chargeable_gain += same_day_gain
                LOGGER.debug(
                    "SAME DAY, quantity %d, gain %s, disposal price %s, "
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
                current_amount -= available_quantity * acquisition_price
                if current_quantity == 0:
                    assert (
                        round_decimal(current_amount, 23) == 0
                    ), f"current amount {current_amount}"
                fees = disposal.fees * available_quantity / original_disposal_quantity
                calculation_entries.append(
                    CalculationEntry(
                        rule_type=RuleType.SAME_DAY,
                        quantity=available_quantity,
                        amount=same_day_proceeds,
                        gain=same_day_gain,
                        allowable_cost=same_day_allowable_cost,
                        fees=fees,
                        new_quantity=current_quantity,
                        new_pool_cost=current_amount,
                    )
                )

        # Bed and breakfast rule next
        if disposal_quantity > 0:
            for i in range(BED_AND_BREAKFAST_DAYS):
                search_index = date_index + datetime.timedelta(days=i + 1)
                if has_key(self.acquisition_list, search_index, symbol):
                    acquisition = self.acquisition_list[search_index][symbol]

                    bnb_acquisition = (
                        self.bnb_list[search_index][symbol]
                        if has_key(self.bnb_list, search_index, symbol)
                        else HmrcTransactionData()
                    )
                    assert bnb_acquisition.quantity <= acquisition.quantity

                    same_day_disposal = (
                        self.disposal_list[search_index][symbol]
                        if has_key(self.disposal_list, search_index, symbol)
                        else HmrcTransactionData()
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
                    print(
                        f"WARNING: Bed and breakfasting for {symbol}."
                        f" Disposed on {date_index}"
                        f" and acquired again on {search_index}"
                    )
                    available_quantity = min(
                        disposal_quantity,
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity,
                    )
                    acquisition_price = acquisition.amount / acquisition.quantity
                    bed_and_breakfast_proceeds = available_quantity * disposal_price
                    bed_and_breakfast_allowable_cost = (
                        available_quantity * acquisition_price
                    )
                    bed_and_breakfast_gain = (
                        bed_and_breakfast_proceeds - bed_and_breakfast_allowable_cost
                    )
                    chargeable_gain += bed_and_breakfast_gain
                    LOGGER.debug(
                        "BED & BREAKFAST, quantity %d, gain %s, disposal price %s, "
                        "acquisition price %s",
                        available_quantity,
                        bed_and_breakfast_gain,
                        disposal_price,
                        acquisition_price,
                    )
                    disposal_quantity -= available_quantity
                    proceeds_amount -= available_quantity * disposal_price
                    current_price = current_amount / current_quantity
                    amount_delta = available_quantity * current_price
                    current_quantity -= available_quantity
                    current_amount -= amount_delta
                    if current_quantity == 0:
                        assert (
                            round_decimal(current_amount, 23) == 0
                        ), f"current amount {current_amount}"
                    add_to_list(
                        self.bnb_list,
                        search_index,
                        symbol,
                        available_quantity,
                        amount_delta,
                        Decimal(0),
                    )
                    fees = (
                        disposal.fees * available_quantity / original_disposal_quantity
                    )
                    calculation_entries.append(
                        CalculationEntry(
                            rule_type=RuleType.BED_AND_BREAKFAST,
                            quantity=available_quantity,
                            amount=bed_and_breakfast_proceeds,
                            gain=bed_and_breakfast_gain,
                            allowable_cost=bed_and_breakfast_allowable_cost,
                            fees=fees,
                            bed_and_breakfast_date_index=search_index,
                            new_quantity=current_quantity,
                            new_pool_cost=current_amount,
                        )
                    )
        if disposal_quantity > 0:
            allowable_cost = current_amount * disposal_quantity / current_quantity
            chargeable_gain += proceeds_amount - allowable_cost
            LOGGER.debug(
                "SECTION 104, quantity %d, gain %s, proceeds amount %s, "
                "allowable cost %s",
                disposal_quantity,
                proceeds_amount - allowable_cost,
                proceeds_amount,
                allowable_cost,
            )
            current_quantity -= disposal_quantity
            current_amount -= allowable_cost
            if current_quantity == 0:
                assert (
                    round_decimal(current_amount, 10) == 0
                ), f"current amount {current_amount}"
            fees = disposal.fees * disposal_quantity / original_disposal_quantity
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
                    quantity=disposal_quantity,
                    amount=proceeds_amount,
                    gain=proceeds_amount - allowable_cost,
                    allowable_cost=allowable_cost,
                    fees=fees,
                    new_quantity=current_quantity,
                    new_pool_cost=current_amount,
                )
            )
            disposal_quantity = Decimal(0)

        assert (
            round_decimal(disposal_quantity, 23) == 0
        ), f"disposal quantity {disposal_quantity}"
        self.portfolio[symbol] = Position(current_quantity, current_amount)
        chargeable_gain = round_decimal(chargeable_gain, 2)
        return chargeable_gain, calculation_entries

    def calculate_capital_gain(
        self,
    ) -> CapitalGainsReport:
        """Calculate capital gain and return generated report."""
        begin_index = INTERNAL_START_DATE
        tax_year_start_index = self.tax_year_start_date
        end_index = self.tax_year_end_date
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        calculation_log: CalculationLog = {}
        self.portfolio.clear()

        for date_index in (
            begin_index + datetime.timedelta(days=x)
            for x in range(0, (end_index - begin_index).days + 1)
        ):
            if date_index in self.acquisition_list:
                for symbol in self.acquisition_list[date_index]:
                    calculation_entries = self.process_acquisition(
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        if date_index not in calculation_log:
                            calculation_log[date_index] = {}
                        calculation_log[date_index][
                            f"buy${symbol}"
                        ] = calculation_entries
            if date_index in self.disposal_list:
                for symbol in self.disposal_list[date_index]:
                    (
                        transaction_capital_gain,
                        calculation_entries,
                    ) = self.process_disposal(
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        disposal_count += 1
                        transaction_disposal_proceeds = self.disposal_list[date_index][
                            symbol
                        ].amount
                        disposal_proceeds += transaction_disposal_proceeds
                        allowable_costs += (
                            transaction_disposal_proceeds - transaction_capital_gain
                        )
                        transaction_quantity = self.disposal_list[date_index][
                            symbol
                        ].quantity
                        LOGGER.debug(
                            "DISPOSAL on %s of %s, quantity %d, capital gain $%s",
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
                            calculated_proceeds += entry.amount
                            calculated_gain += entry.gain
                        assert transaction_quantity == calculated_quantity
                        assert round_decimal(
                            transaction_disposal_proceeds, 10
                        ) == round_decimal(
                            calculated_proceeds, 10
                        ), f"{transaction_disposal_proceeds} != {calculated_proceeds}"
                        assert transaction_capital_gain == round_decimal(
                            calculated_gain, 2
                        )
                        if date_index not in calculation_log:
                            calculation_log[date_index] = {}
                        calculation_log[date_index][
                            f"sell${symbol}"
                        ] = calculation_entries
                        if transaction_capital_gain > 0:
                            capital_gain += transaction_capital_gain
                        else:
                            capital_loss += transaction_capital_gain
        print("\nSecond pass completed")
        allowance = CAPITAL_GAIN_ALLOWANCES.get(self.tax_year)

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
            calculation_log,
            show_unrealized_gains=self.calc_unrealized_gains,
        )

    def make_portfolio_entry(
        self, symbol: str, quantity: Decimal, amount: Decimal
    ) -> PortfolioEntry:
        """Create a portfolio entry in the report."""
        # (by calculating the unrealized gains)
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


def main() -> int:
    """Run main function."""
    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True
    args = create_parser().parse_args()

    if args.version:
        print(f"cgt-calc {importlib.metadata.version(__package__)}")
        return 0

    if args.report == "":
        print("error: report name can't be empty")
        return 1

    default_logging_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=default_logging_level)

    # Read data from input files
    broker_transactions = read_broker_transactions(
        args.schwab,
        args.schwab_award,
        args.schwab_equity_award_json,
        args.trading212,
        args.mssb,
        args.sharesight,
        args.raw,
    )
    converter = CurrencyConverter(args.exchange_rates_file)
    initial_prices = InitialPrices(read_initial_prices(args.initial_prices))
    price_fetcher = CurrentPriceFetcher(converter)

    calculator = CapitalGainsCalculator(
        args.year,
        converter,
        price_fetcher,
        initial_prices,
        balance_check=args.balance_check,
        calc_unrealized_gains=args.calc_unrealized_gains,
    )
    # First pass converts broker transactions to HMRC transactions.
    # This means applying same day rule and collapsing all transactions with
    # same type within the same day.
    # It also converts prices to GBP, validates data and calculates dividends,
    # taxes on dividends and interest.
    calculator.convert_to_hmrc_transactions(broker_transactions)
    # Second pass calculates capital gain tax for the given tax year.
    report = calculator.calculate_capital_gain()
    print(report)

    # Generate PDF report.
    if not args.no_report:
        render_latex.render_calculations(
            report,
            output_path=Path(args.report),
            skip_pdflatex=args.no_pdflatex,
        )
    print("All done!")

    return 0


def init() -> None:
    """Entry point."""
    sys.exit(main())


if __name__ == "__main__":
    init()
