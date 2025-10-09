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
from .const import (
    BED_AND_BREAKFAST_DAYS,
    CAPITAL_GAIN_ALLOWANCES,
    COUNTRY_CURRENCY,
    DIVIDEND_ALLOWANCES,
    DIVIDEND_DOUBLE_TAXATION_RULES,
    ERI_TAX_DATE_DELTA,
    INTERNAL_START_DATE,
)
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
from .isin_converter import IsinConverter
from .model import (
    ActionType,
    BrokerTransaction,
    CalcuationType,
    CalculationEntry,
    CalculationLog,
    CapitalGainsReport,
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
from .parsers import read_broker_transactions, read_initial_prices
from .parsers.eri.data_import import eri_import_from_path
from .spin_off_handler import SpinOffHandler
from .transaction_log import add_to_list, has_key
from .util import approx_equal, round_decimal

LOGGER = logging.getLogger(__name__)


def get_amount_or_fail(transaction: BrokerTransaction) -> Decimal:
    """Return the transaction amount or throw an error."""
    amount = transaction.amount
    if amount is None:
        raise AmountMissingError(transaction)
    return amount


def get_symbol_or_fail(transaction: BrokerTransaction) -> str:
    """Return the transaction symbol or throw an error."""
    symbol = transaction.symbol
    if symbol is None:
        raise SymbolMissingError(transaction)
    return symbol


# Amount difference can be caused by rounding errors in the price.
# Schwab rounds down the price to 4 decimal places
#  so that the error in amount can be more than $0.01.
# Fox example:
# 500 shares of FOO sold at $100.00016 with $1.23 fees.
# "01/01/2024,"Sell","FOO","FOO","500","$100.0001","$1.23","$49,998.85"
# calculated_amount = 500 * 100.0001 - 1.23 = 49998.82
# amount_on_record = 49998.85 vs calculated_amount = 49998.82
def _approx_equal_price_rounding(
    amount_on_record: Decimal,
    quantity_on_record: Decimal,
    price_on_record: Decimal,
    fees_on_record: Decimal,
    calcuationType: CalcuationType,
) -> bool:
    calculated_amount = Decimal(0)
    calculated_price = Decimal(0)
    if calcuationType is CalcuationType.ACQUISITION:
        calculated_amount = Decimal(-1) * (
            quantity_on_record * price_on_record + fees_on_record
        )
        calculated_price = (
            Decimal(-1) * amount_on_record - fees_on_record
        ) / quantity_on_record
    elif calcuationType is CalcuationType.DISPOSAL:
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
        balance_check: bool = True,
        calc_unrealized_gains: bool = False,
    ):
        """Create calculator object."""
        self.tax_year = tax_year

        self.tax_year_start_date = get_tax_year_start(tax_year)
        self.tax_year_end_date = get_tax_year_end(tax_year)

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

        self.acquisition_list: HmrcTransactionLog = {}
        self.disposal_list: HmrcTransactionLog = {}
        self.bnb_list: HmrcTransactionLog = {}

        self.dividend_list: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        self.dividend_tax_list: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        self.interest_list: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)

        # Log for the report section related only to interests and dividends
        self.calculation_log_yields: CalculationLog = defaultdict(dict)

        self.portfolio: dict[str, Position] = defaultdict(Position)
        self.spin_offs: dict[datetime.date, list[SpinOff]] = defaultdict(list)
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
        """Add new acquisition to the given list."""
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
                CalcuationType.ACQUISITION,
            ):
                raise CalculatedAmountDiscrepancyError(transaction, -calculated_amount)
            amount = -amount

        self.portfolio[symbol] += Position(quantity, amount)

        add_to_list(
            self.acquisition_list,
            transaction.date,
            symbol,
            quantity,
            self.currency_converter.to_gbp_for(amount, transaction),
            self.currency_converter.to_gbp_for(transaction.fees, transaction),
        )

    def handle_spin_off(
        self,
        transaction: BrokerTransaction,
    ) -> tuple[Decimal, Decimal]:
        """Handle spin off transaction.

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
        symbol = get_symbol_or_fail(transaction)
        quantity = transaction.quantity
        assert quantity is not None

        ticker = self.spin_off_handler.get_spin_off_source(
            symbol, transaction.date, self.portfolio
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
                cost_proportion=share_of_original_cost,
                date=transaction.date,
            )
        )

        amount = (1 - share_of_original_cost) * original_src_amount
        return amount / quantity, round_decimal(amount, 2)

    def add_disposal(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Add new disposal to the given list."""
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
        calculated_amount = quantity * price - transaction.fees
        if not _approx_equal_price_rounding(
            amount,
            quantity,
            price,
            transaction.fees,
            CalcuationType.DISPOSAL,
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

    def add_eri(
        self,
        transaction: BrokerTransaction,
    ) -> None:
        """Add an Excess Reported Income to the list.

        UK has a specific tax regime which applies to UK investors in offshore
        funds.

        https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds

        Example of UK offshore funds are the most common UCITS ETFs (Vanguard,
        Blackrock, XTrackers) that are normally located in Ireland.

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

        Fund reports are directly provided in the fund website (i.e. Blackrock,
        Vanguard, XTrackers, etc) on a yearly fashion.

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

        assert transaction.isin is not None, f"{transaction} doesn't have a valid ISIN"
        assert transaction.price is not None, (
            f"{transaction} doesn't have a valid price"
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
                        print(
                            "WARNING: Skipping duplicated ERI transaction: "
                            f"{transaction}"
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

    def convert_to_hmrc_transactions(
        self,
        transactions: list[BrokerTransaction],
    ) -> None:
        """Convert broker transactions to HMRC transactions."""
        # We keep a balance per broker,currency pair
        balance: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        dividends: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        dividends_tax: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        interests: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        total_disposal_proceeds = Decimal(0)
        balance_history: list[Decimal] = []

        for transaction in transactions:
            self.isin_converter.add_from_transaction(transaction)

        for i, transaction in enumerate(transactions):
            if transaction.action == ActionType.EXCESS_REPORTED_INCOME:
                self.add_eri(transaction)
                continue

            new_balance = balance[(transaction.broker, transaction.currency)]
            if transaction.action is ActionType.TRANSFER:
                new_balance += get_amount_or_fail(transaction)
            elif transaction.action in [
                ActionType.BUY,
                ActionType.REINVEST_SHARES,
            ]:
                new_balance += get_amount_or_fail(transaction)
                self.add_acquisition(transaction)
            elif transaction.action in [ActionType.SELL, ActionType.CASH_MERGER]:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                self.add_disposal(transaction)
                if self.date_in_tax_year(transaction.date):
                    total_disposal_proceeds += self.currency_converter.to_gbp_for(
                        amount + transaction.fees, transaction
                    )
            elif transaction.action is ActionType.FEE:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                transaction.fees = -amount
                transaction.quantity = Decimal(0)
                gbp_fees = self.currency_converter.to_gbp_for(
                    transaction.fees, transaction
                )
                symbol = get_symbol_or_fail(transaction)
                add_to_list(
                    self.acquisition_list,
                    transaction.date,
                    symbol,
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
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                self.dividend_list[(symbol, transaction.date)] += ForeignCurrencyAmount(
                    amount, currency
                )
                if self.date_in_tax_year(transaction.date):
                    dividends[(symbol, currency)] += amount
            elif transaction.action is ActionType.DIVIDEND_TAX:
                amount = get_amount_or_fail(transaction)
                symbol = get_symbol_or_fail(transaction)
                currency = transaction.currency
                new_balance += amount
                self.dividend_tax_list[(symbol, transaction.date)] += (
                    ForeignCurrencyAmount(amount, currency)
                )
                if self.date_in_tax_year(transaction.date):
                    dividends_tax[(symbol, currency)] += amount
            elif transaction.action is ActionType.ADJUSTMENT:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
            elif transaction.action is ActionType.INTEREST:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
                self.interest_list[(transaction.broker, transaction.date)] += (
                    ForeignCurrencyAmount(amount, transaction.currency)
                )
                if self.date_in_tax_year(transaction.date):
                    interests[(transaction.broker, transaction.currency)] += amount
            elif transaction.action is ActionType.WIRE_FUNDS_RECEIVED:
                amount = get_amount_or_fail(transaction)
                new_balance += amount
            elif transaction.action is ActionType.REINVEST_DIVIDENDS:
                print(f"WARNING: Ignoring unsupported action: {transaction.action}")
            else:
                raise InvalidTransactionError(
                    transaction, f"Action not processed({transaction.action})"
                )
            balance_history.append(new_balance)
            if self.balance_check and new_balance < 0:
                msg = f"Reached a negative balance({new_balance})"
                msg += f" for broker {transaction.broker} ({transaction.currency})"
                msg += " after processing the following transactions:\n"
                msg += "\n".join(
                    [
                        f"{trx}\nBalance after transaction={balance_after}"
                        for trx, balance_after in zip(
                            transactions[: i + 1], balance_history, strict=True
                        )
                    ]
                )
                raise CalculationError(msg)
            balance[(transaction.broker, transaction.currency)] = new_balance

        self.first_pass_report(
            balance, dividends, dividends_tax, interests, total_disposal_proceeds
        )

    def first_pass_report(
        self,
        balance: dict[tuple[str, str], Decimal],
        dividends: dict[tuple[str, str], Decimal],
        dividends_tax: dict[tuple[str, str], Decimal],
        interests: dict[tuple[str, str], Decimal],
        total_disposal_proceeds: Decimal,
    ) -> None:
        """Print the results of the first pass."""
        print("First pass completed")
        print("Final portfolio:")
        for stock, position in self.portfolio.items():
            print(f"  {stock}: {position}")
        print("Final balance:")
        for (broker, currency), amount in balance.items():
            print(f"  {broker}: {round_decimal(amount, 2)} ({currency})")
        if dividends:
            print("Dividends:")
            for (symbol, currency), amount in dividends.items():
                tax = dividends_tax[(symbol, currency)]
                tax_str = f", excluding {-tax} taxed at source" if tax < 0 else ""
                print(f"  {symbol}: {round_decimal(amount, 2)}{tax_str} ({currency})")
        if interests:
            print("Interests:")
            for (broker, currency), amount in interests.items():
                print(f"  {broker}: {round_decimal(amount, 2)} ({currency})")
        print(f"Disposal proceeds: £{round_decimal(total_disposal_proceeds, 2)}")
        print()

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
            spin_off = next(
                (
                    spin_off
                    for spin_off in self.spin_offs[date_index]
                    if spin_off.dest == symbol
                ),
                None,
            )
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
    ) -> tuple[Decimal, list[CalculationEntry], CalculationEntry | None]:
        """Process single disposal."""
        disposal = self.disposal_list[date_index][symbol]
        disposal_quantity = disposal.quantity
        proceeds_amount = disposal.amount
        original_disposal_quantity = disposal_quantity
        disposal_price = proceeds_amount / disposal_quantity
        current_quantity = self.portfolio[symbol].quantity
        spin_off_entry = None

        for date, spin_offs in self.spin_offs.items():
            if date > date_index:
                continue
            for spin_off in spin_offs:
                # Up to the actual spin-off happening all the sales has to happen based
                # on original cost basis, after spin-off we have to consider its impact
                # for all future trades
                amount = self.portfolio[spin_off.source].amount
                quantity = self.portfolio[spin_off.source].quantity
                new_amount = amount * spin_off.cost_proportion
                LOGGER.debug(
                    "Detected spin-off of %s to %s on %s, modyfing the cost amount "
                    "from %d to %d according to cost-proportion: %.2f",
                    spin_off.source,
                    spin_off.dest,
                    spin_off.date,
                    amount,
                    new_amount,
                    spin_off.cost_proportion,
                )
                self.spin_offs[date] = spin_offs[1:]
                self.portfolio[spin_off.source].amount = new_amount
                spin_off_entry = CalculationEntry(
                    RuleType.SPIN_OFF,
                    quantity=quantity,
                    amount=-amount,
                    new_quantity=quantity,
                    gain=None,
                    # Fees, if any are already accounted on the acquisition of
                    # spined off shares
                    fees=Decimal(0),
                    new_pool_cost=new_amount,
                    allowable_cost=new_amount,
                    spin_off=spin_off,
                )

        current_amount = self.portfolio[symbol].amount
        assert disposal_quantity <= current_quantity
        chargeable_gain = Decimal(0)
        calculation_entries = []
        # Same day rule is first
        if has_key(self.acquisition_list, date_index, symbol):
            same_day_acquisition = self.acquisition_list[date_index][symbol]

            available_quantity = min(disposal_quantity, same_day_acquisition.quantity)
            if available_quantity > 0:
                fees = disposal.fees * available_quantity / original_disposal_quantity
                acquisition_price = (
                    same_day_acquisition.amount / same_day_acquisition.quantity
                )
                same_day_amount = available_quantity * disposal_price
                same_day_proceeds = same_day_amount + fees
                same_day_allowable_cost = available_quantity * acquisition_price + fees
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
                    assert round_decimal(current_amount, 23) == 0, (
                        f"current amount {current_amount}"
                    )
                calculation_entries.append(
                    CalculationEntry(
                        rule_type=RuleType.SAME_DAY,
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

            for i in range(BED_AND_BREAKFAST_DAYS):
                search_index = date_index + datetime.timedelta(days=i + 1)

                # ERI are distributed annually but when a fund close we might have
                # multiple ERI distribution in close succession
                eri = self.get_eri(symbol, search_index)
                if eri:
                    eris.append(eri)
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
                    fees = (
                        disposal.fees * available_quantity / original_disposal_quantity
                    )
                    acquisition_price = acquisition.amount / acquisition.quantity
                    bed_and_breakfast_amount = available_quantity * disposal_price
                    bed_and_breakfast_proceeds = bed_and_breakfast_amount + fees
                    bed_and_breakfast_allowable_cost = (
                        available_quantity * acquisition_price
                    ) + fees
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
                        "BED & BREAKFAST, quantity %d, gain %s, disposal price %s, "
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
                    current_price = current_amount / current_quantity
                    amount_delta = available_quantity * current_price
                    current_quantity -= available_quantity
                    current_amount -= amount_delta
                    if current_quantity == 0:
                        assert round_decimal(current_amount, 23) == 0, (
                            f"current amount {current_amount}"
                        )
                    add_to_list(
                        self.bnb_list,
                        search_index,
                        symbol,
                        available_quantity,
                        amount_delta + total_dist_amount,
                        Decimal(0),
                        eris,
                    )
                    calculation_entries.append(
                        CalculationEntry(
                            rule_type=RuleType.BED_AND_BREAKFAST,
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
        if disposal_quantity > 0:
            available_quantity = disposal_quantity
            fees = disposal.fees * available_quantity / original_disposal_quantity
            acquisition_price = current_amount / current_quantity
            r104_amount = available_quantity * disposal_price
            r104_proceeds = r104_amount + fees
            r104_allowable_cost = available_quantity * acquisition_price + fees
            r104_gain = r104_proceeds - r104_allowable_cost
            chargeable_gain += r104_gain
            LOGGER.debug(
                "SECTION 104, quantity %d, gain %s, proceeds amount %s, "
                "allowable cost %s",
                available_quantity,
                r104_gain,
                r104_proceeds,
                r104_allowable_cost,
            )
            disposal_quantity -= available_quantity
            proceeds_amount -= available_quantity * disposal_price
            current_price = current_amount / current_quantity
            amount_delta = available_quantity * current_price
            current_quantity -= available_quantity
            current_amount -= amount_delta
            if current_quantity == 0:
                assert round_decimal(current_amount, 10) == 0, (
                    f"current amount {current_amount}"
                )
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
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
        self.portfolio[symbol] = Position(current_quantity, current_amount)
        chargeable_gain = round_decimal(chargeable_gain, 2)
        return (
            chargeable_gain,
            calculation_entries,
            spin_off_entry,
        )

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
            "modyfing the cost amount from %d to %d",
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

    def process_interests(self) -> None:
        """Process all interest events.

        It groups them by month, using the last date on each month for the report
        and updates the interest totals for the year.
        """
        monthly_interests: ForeignAmountLog = defaultdict(ForeignCurrencyAmount)
        last_date: datetime.date = datetime.date.min
        last_broker: str | None = None
        # sort by broker and date
        for (broker, date), foreign_amount in sorted(self.interest_list.items()):
            if self.date_in_tax_year(date):
                # If it's still the same month bring forward the value to the current
                # date
                if broker == last_broker and date.replace(day=1) == last_date.replace(
                    day=1
                ):
                    monthly_interests[(broker, date)] = monthly_interests.pop(
                        (broker, last_date)
                    )
                monthly_interests[(broker, date)] += foreign_amount
                last_date = date
                last_broker = broker

        for (broker, date), foreign_amount in monthly_interests.items():
            gbp_amount = self.currency_converter.to_gbp(
                foreign_amount.amount, foreign_amount.currency, date
            )
            if foreign_amount.currency == COUNTRY_CURRENCY:
                self.total_uk_interest += gbp_amount
                rule_prefix = "interestUK"
            else:
                self.total_foreign_interest += gbp_amount
                rule_prefix = "interestForeign"

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

    def process_dividends(self) -> None:
        """Process all dividends events and taxes.

        It updates the interest total for the year if needed.
        """
        for (symbol, date), foreign_amount in self.dividend_list.items():
            tax = self.dividend_tax_list[(symbol, date)]

            treaty = None
            is_interest_fund = symbol in self.interest_fund_tickers

            if tax.amount < 0:
                if is_interest_fund:
                    LOGGER.warning(
                        "Cannot apply taxation treaty for bond fund %s", symbol
                    )
                elif foreign_amount.currency != COUNTRY_CURRENCY:
                    assert tax.currency == foreign_amount.currency, (
                        f"Not matching currency for dividend {foreign_amount.currency} "
                        f"and its tax {tax.currency}"
                    )
                    try:
                        treaty = DIVIDEND_DOUBLE_TAXATION_RULES[foreign_amount.currency]
                    except KeyError:
                        LOGGER.warning(
                            "Taxation treaty for %s country is missing (ticker: %s), "
                            "double taxation rules cannot be determined!",
                            foreign_amount.currency,
                            symbol,
                        )
                        treaty = None
                    else:
                        assert treaty is not None
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
                foreign_amount.amount, foreign_amount.currency, date
            )
            tax_amount = self.currency_converter.to_gbp(
                tax.amount, foreign_amount.currency, date
            )

            if self.date_in_tax_year(date):
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
        """Calculate capital gain and return generated report."""
        begin_index = INTERNAL_START_DATE
        tax_year_start_index = self.tax_year_start_date
        end_index = self.tax_year_end_date
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        self.portfolio.clear()

        calculation_log: CalculationLog = defaultdict(dict)

        for date_index in (
            begin_index + datetime.timedelta(days=x)
            for x in range((end_index - begin_index).days + 1)
        ):
            if date_index in self.acquisition_list:
                for symbol in self.acquisition_list[date_index]:
                    calculation_entries = self.process_acquisition(
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"buy${symbol}"] = (
                            calculation_entries
                        )
            if date_index in self.disposal_list:
                for symbol in self.disposal_list[date_index]:
                    (
                        transaction_capital_gain,
                        calculation_entries,
                        spin_off_entry,
                    ) = self.process_disposal(
                        symbol,
                        date_index,
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
                        calculation_log[date_index][f"sell${symbol}"] = (
                            calculation_entries
                        )
                        if transaction_capital_gain > 0:
                            capital_gain += transaction_capital_gain
                        else:
                            capital_loss += transaction_capital_gain
                        if spin_off_entry is not None:
                            spin_off = spin_off_entry.spin_off
                            assert spin_off is not None
                            calculation_log[spin_off.date][
                                f"spin-off${spin_off.source}"
                            ] = [spin_off_entry]

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

        print("\nSecond pass completed")
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

    isin_translation_file = args.isin_translation_file
    # If reports mode
    if args.import_eri_reports:
        isin_translation_file = eri_import_from_path(args.import_eri_reports)

    # Read data from input files
    broker_transactions = read_broker_transactions(
        args.schwab,
        args.schwab_award,
        args.schwab_equity_award_json,
        args.trading212,
        args.mssb,
        args.sharesight,
        args.raw,
        args.vanguard,
        args.eri_raw_file,
    )
    currency_converter = CurrencyConverter(args.exchange_rates_file)
    initial_prices = InitialPrices(read_initial_prices(args.initial_prices))
    price_fetcher = CurrentPriceFetcher(currency_converter)
    spin_off_handler = SpinOffHandler(args.spin_offs_file)
    isin_converter = IsinConverter(isin_translation_file)

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
