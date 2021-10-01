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
from .dates import (
    date_from_index,
    date_to_index,
    get_tax_year_end,
    get_tax_year_start,
    is_date,
)
from .exceptions import (
    AmountMissingError,
    CalculatedAmountDiscrepancy,
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
    HmrcTransactionLog,
    RuleType,
)
from .parsers import (
    read_broker_transactions,
    read_gbp_prices_history,
    read_initial_prices,
)
from .transaction_log import add_to_list, has_key
from .util import round_decimal

LOGGER = logging.getLogger(__name__)


class CapitalGainsCalculator:
    """Main calculator class."""

    def __init__(
        self, tax_year: int, converter: CurrencyConverter, initial_prices: InitialPrices
    ):
        """Create calculator object."""
        self.tax_year = tax_year

        self.tax_year_start_date = get_tax_year_start(tax_year)
        self.tax_year_end_date = get_tax_year_end(tax_year)

        self.converter = converter
        self.initial_prices = initial_prices

    def date_in_tax_year(self, date: datetime.date) -> bool:
        """Check if date is within current tax year."""
        assert is_date(date)
        return self.tax_year_start_date <= date <= self.tax_year_end_date

    def add_acquisition(
        self,
        portfolio: dict[str, Decimal],
        acquisition_list: HmrcTransactionLog,
        transaction: BrokerTransaction,
    ) -> None:
        """Add new acquisition to the given list."""
        symbol = transaction.symbol
        quantity = transaction.quantity
        if symbol is None:
            raise SymbolMissingError(transaction)
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        # This is basically only for data validation
        if symbol in portfolio:
            portfolio[symbol] += quantity
        else:
            portfolio[symbol] = quantity
        # Add to acquisition_list to apply same day rule
        if transaction.action in [ActionType.STOCK_ACTIVITY, ActionType.SPIN_OFF]:
            stock_price_gbp = None

            if transaction.price is not None and transaction.currency is not None:
                stock_price_gbp = self.converter.to_gbp(
                    transaction.price, transaction.currency, transaction.date
                )
            else:
                stock_price_gbp = self.initial_prices.get(transaction.date, symbol)

            amount = quantity * stock_price_gbp
        else:
            if transaction.amount is None:
                raise AmountMissingError(transaction)
            if transaction.price is None:
                raise PriceMissingError(transaction)
            calculated_amount = round_decimal(
                quantity * transaction.price + transaction.fees, 2
            )
            if transaction.amount != -calculated_amount:
                raise CalculatedAmountDiscrepancy(transaction, -calculated_amount)
            amount = -transaction.amount
        add_to_list(
            acquisition_list,
            date_to_index(transaction.date),
            symbol,
            quantity,
            self.converter.to_gbp_for(amount, transaction),
            self.converter.to_gbp_for(transaction.fees, transaction),
        )

    def add_disposal(
        self,
        portfolio: dict[str, Decimal],
        disposal_list: HmrcTransactionLog,
        transaction: BrokerTransaction,
    ) -> None:
        """Add new disposal to the given list."""
        symbol = transaction.symbol
        quantity = transaction.quantity
        if symbol is None:
            raise SymbolMissingError(transaction)
        if symbol not in portfolio:
            raise InvalidTransactionError(
                transaction, "Tried to sell not owned symbol, reversed order?"
            )
        if quantity is None or quantity <= 0:
            raise QuantityNotPositiveError(transaction)
        if portfolio[symbol] < quantity:
            raise InvalidTransactionError(
                transaction,
                f"Tried to sell more than the available balance({portfolio[symbol]})",
            )
        # This is basically only for data validation
        portfolio[symbol] -= quantity
        if portfolio[symbol] == 0:
            del portfolio[symbol]
        # Add to disposal_list to apply same day rule
        if transaction.amount is None:
            raise AmountMissingError(transaction)
        if transaction.price is None:
            raise PriceMissingError(transaction)
        amount = transaction.amount
        calculated_amount = round_decimal(
            quantity * transaction.price - transaction.fees, 2
        )
        if amount != calculated_amount:
            raise CalculatedAmountDiscrepancy(transaction, calculated_amount)
        add_to_list(
            disposal_list,
            date_to_index(transaction.date),
            symbol,
            quantity,
            self.converter.to_gbp_for(amount, transaction),
            self.converter.to_gbp_for(transaction.fees, transaction),
        )

    def convert_to_hmrc_transactions(
        self,
        transactions: list[BrokerTransaction],
    ) -> tuple[HmrcTransactionLog, HmrcTransactionLog]:
        """Convert broker transactions to HMRC transactions."""
        # We keep a balance per broker,currency pair
        balance: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        dividends = Decimal(0)
        dividends_tax = Decimal(0)
        interest = Decimal(0)
        total_sells = Decimal(0)
        portfolio: dict[str, Decimal] = {}
        acquisition_list: HmrcTransactionLog = {}
        disposal_list: HmrcTransactionLog = {}

        for i, transaction in enumerate(transactions):
            new_balance = balance[(transaction.broker, transaction.currency)]
            if transaction.action is ActionType.TRANSFER:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
            elif transaction.action is ActionType.BUY:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
                self.add_acquisition(portfolio, acquisition_list, transaction)
            elif transaction.action is ActionType.SELL:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
                self.add_disposal(portfolio, disposal_list, transaction)
                if self.date_in_tax_year(transaction.date):
                    total_sells += self.converter.to_gbp_for(
                        transaction.amount, transaction
                    )
            elif transaction.action is ActionType.FEE:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
                transaction.fees = -transaction.amount
                transaction.quantity = Decimal(0)
                gbp_fees = self.converter.to_gbp_for(transaction.fees, transaction)
                if transaction.symbol is None:
                    raise SymbolMissingError(transaction)
                add_to_list(
                    acquisition_list,
                    date_to_index(transaction.date),
                    transaction.symbol,
                    transaction.quantity,
                    gbp_fees,
                    gbp_fees,
                )
            elif transaction.action in [ActionType.STOCK_ACTIVITY, ActionType.SPIN_OFF]:
                self.add_acquisition(portfolio, acquisition_list, transaction)
            elif transaction.action in [ActionType.DIVIDEND, ActionType.CAPITAL_GAIN]:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
                if self.date_in_tax_year(transaction.date):
                    dividends += self.converter.to_gbp_for(
                        transaction.amount, transaction
                    )
            elif transaction.action in [ActionType.TAX, ActionType.ADJUSTMENT]:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
                if self.date_in_tax_year(transaction.date):
                    dividends_tax += self.converter.to_gbp_for(
                        transaction.amount, transaction
                    )
            elif transaction.action is ActionType.INTEREST:
                if transaction.amount is None:
                    raise AmountMissingError(transaction)
                new_balance += transaction.amount
                if self.date_in_tax_year(transaction.date):
                    interest += self.converter.to_gbp_for(
                        transaction.amount, transaction
                    )
            else:
                raise InvalidTransactionError(
                    transaction, f"Action not processed({transaction.action})"
                )
            if new_balance < 0:
                msg = f"Reached a negative balance({new_balance})"
                msg += f" for broker {transaction.broker} ({transaction.currency})"
                msg += " after processing the following transactions:\n"
                msg += "\n".join(map(str, transactions[: i + 1]))
                raise CalculationError(msg)
            balance[(transaction.broker, transaction.currency)] = new_balance
        print("First pass completed")
        print("Final portfolio:")
        for stock, quantity in portfolio.items():
            print(f"  {stock}: {round_decimal(quantity, 2)}")
        print("Final balance:")
        for (broker, currency), amount in balance.items():
            print(f"  {broker}: {round_decimal(amount, 2)} ({currency})")
        print(f"Dividends: £{round_decimal(dividends, 2)}")
        print(f"Dividend taxes: £{round_decimal(-dividends_tax, 2)}")
        print(f"Interest: £{round_decimal(interest, 2)}")
        print(f"Disposal proceeds: £{round_decimal(total_sells, 2)}")
        print("")
        return acquisition_list, disposal_list

    @staticmethod
    def process_acquisition(
        acquisition_list: HmrcTransactionLog,
        bed_and_breakfast_list: HmrcTransactionLog,
        portfolio: dict[str, tuple[Decimal, Decimal]],
        symbol: str,
        date_index: int,
    ) -> list[CalculationEntry]:
        """Process single acquisition."""
        acquisition_quantity, acquisition_amount, acquisition_fees = acquisition_list[
            date_index
        ][symbol]
        original_acquisition_amount = acquisition_amount
        if symbol not in portfolio:
            portfolio[symbol] = (Decimal(0), Decimal(0))
        current_quantity, current_amount = portfolio[symbol]
        calculation_entries = []

        # Management fee transaction can have 0 quantity
        assert acquisition_quantity >= 0
        assert acquisition_amount > 0
        bed_and_breakfast_quantity = Decimal(0)
        bed_and_breakfast_amount = Decimal(0)
        bed_and_breakfast_fees = Decimal(0)
        if acquisition_quantity > 0:
            acquisition_price = acquisition_amount / acquisition_quantity
            if has_key(bed_and_breakfast_list, date_index, symbol):
                (
                    bed_and_breakfast_quantity,
                    bed_and_breakfast_amount,
                    _bed_and_breakfast_fees,
                ) = bed_and_breakfast_list[date_index][symbol]
                assert bed_and_breakfast_quantity <= acquisition_quantity
                acquisition_amount -= bed_and_breakfast_quantity * acquisition_price
                acquisition_amount += bed_and_breakfast_amount
                assert acquisition_amount > 0
                bed_and_breakfast_fees = (
                    acquisition_fees * bed_and_breakfast_quantity / acquisition_quantity
                )
                calculation_entries.append(
                    CalculationEntry(
                        rule_type=RuleType.BED_AND_BREAKFAST,
                        quantity=bed_and_breakfast_quantity,
                        amount=-bed_and_breakfast_amount,
                        new_quantity=current_quantity + bed_and_breakfast_quantity,
                        new_pool_cost=current_amount + bed_and_breakfast_amount,
                        fees=bed_and_breakfast_fees,
                        allowable_cost=original_acquisition_amount,
                    )
                )
        portfolio[symbol] = (
            current_quantity + acquisition_quantity,
            current_amount + acquisition_amount,
        )
        if (
            acquisition_quantity - bed_and_breakfast_quantity > 0
            or bed_and_breakfast_quantity == 0
        ):
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
                    quantity=acquisition_quantity - bed_and_breakfast_quantity,
                    amount=-(acquisition_amount - bed_and_breakfast_amount),
                    new_quantity=current_quantity + acquisition_quantity,
                    new_pool_cost=current_amount + acquisition_amount,
                    fees=acquisition_fees - bed_and_breakfast_fees,
                    allowable_cost=original_acquisition_amount,
                )
            )
        return calculation_entries

    @staticmethod
    def process_disposal(
        acquisition_list: HmrcTransactionLog,
        disposal_list: HmrcTransactionLog,
        bed_and_breakfast_list: HmrcTransactionLog,
        portfolio: dict[str, tuple[Decimal, Decimal]],
        symbol: str,
        date_index: int,
    ) -> tuple[Decimal, list[CalculationEntry]]:
        """Process single disposal."""
        disposal_quantity, proceeds_amount, disposal_fees = disposal_list[date_index][
            symbol
        ]
        original_disposal_quantity = disposal_quantity
        disposal_price = proceeds_amount / disposal_quantity
        current_quantity, current_amount = portfolio[symbol]
        assert disposal_quantity <= current_quantity
        chargeable_gain = Decimal(0)
        calculation_entries = []
        # Same day rule is first
        if has_key(acquisition_list, date_index, symbol):
            same_day_quantity, same_day_amount, _same_day_fees = acquisition_list[
                date_index
            ][symbol]
            available_quantity = min(disposal_quantity, same_day_quantity)
            if available_quantity > 0:
                acquisition_price = same_day_amount / same_day_quantity
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
                    assert current_amount == 0, f"current amount {current_amount}"
                fees = disposal_fees * available_quantity / original_disposal_quantity
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
                search_index = date_index + i + 1
                if has_key(acquisition_list, search_index, symbol):
                    (
                        acquisition_quantity,
                        acquisition_amount,
                        _acquisition_fees,
                    ) = acquisition_list[search_index][symbol]

                    bed_and_breakfast_quantity = Decimal(0)
                    if has_key(bed_and_breakfast_list, search_index, symbol):
                        (
                            bed_and_breakfast_quantity,
                            _bb_amount,
                            _bb_fees,
                        ) = bed_and_breakfast_list[search_index][symbol]
                    assert bed_and_breakfast_quantity <= acquisition_quantity

                    same_day_quantity = Decimal(0)
                    if has_key(disposal_list, search_index, symbol):
                        (
                            same_day_quantity,
                            _same_day_amount,
                            _same_day_fees,
                        ) = disposal_list[search_index][symbol]
                    assert same_day_quantity <= acquisition_quantity

                    # This can be some management fee entry or already used
                    # by bed and breakfast rule
                    if (
                        acquisition_quantity
                        - same_day_quantity
                        - bed_and_breakfast_quantity
                        == 0
                    ):
                        continue
                    print(
                        f"WARNING: Bed and breakfasting for {symbol}."
                        f" Disposed on {date_from_index(date_index)}"
                        f" and acquired again on {date_from_index(search_index)}"
                    )
                    available_quantity = min(
                        disposal_quantity,
                        acquisition_quantity
                        - same_day_quantity
                        - bed_and_breakfast_quantity,
                    )
                    acquisition_price = acquisition_amount / acquisition_quantity
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
                        assert current_amount == 0, f"current amount {current_amount}"
                    add_to_list(
                        bed_and_breakfast_list,
                        search_index,
                        symbol,
                        available_quantity,
                        amount_delta,
                        Decimal(0),
                    )
                    fees = (
                        disposal_fees * available_quantity / original_disposal_quantity
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
            fees = disposal_fees * disposal_quantity / original_disposal_quantity
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
        portfolio[symbol] = (current_quantity, current_amount)
        chargeable_gain = round_decimal(chargeable_gain, 2)
        return chargeable_gain, calculation_entries

    def calculate_capital_gain(
        self,
        acquisition_list: HmrcTransactionLog,
        disposal_list: HmrcTransactionLog,
    ) -> CapitalGainsReport:
        """Calculate capital gain and return generated report."""
        begin_index = date_to_index(INTERNAL_START_DATE)
        tax_year_start_index = date_to_index(self.tax_year_start_date)
        end_index = date_to_index(self.tax_year_end_date)
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        bed_and_breakfast_list: HmrcTransactionLog = {}
        portfolio: dict[str, tuple[Decimal, Decimal]] = {}
        calculation_log: CalculationLog = {}
        for date_index in range(begin_index, end_index + 1):
            if date_index in acquisition_list:
                for symbol in acquisition_list[date_index]:
                    calculation_entries = self.process_acquisition(
                        acquisition_list,
                        bed_and_breakfast_list,
                        portfolio,
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        if date_index not in calculation_log:
                            calculation_log[date_index] = {}
                        calculation_log[date_index][
                            f"buy${symbol}"
                        ] = calculation_entries
            if date_index in disposal_list:
                for symbol in disposal_list[date_index]:
                    (
                        transaction_capital_gain,
                        calculation_entries,
                    ) = self.process_disposal(
                        acquisition_list,
                        disposal_list,
                        bed_and_breakfast_list,
                        portfolio,
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        disposal_count += 1
                        transaction_disposal_proceeds = disposal_list[date_index][
                            symbol
                        ][1]
                        disposal_proceeds += transaction_disposal_proceeds
                        allowable_costs += (
                            transaction_disposal_proceeds - transaction_capital_gain
                        )
                        transaction_quantity = disposal_list[date_index][symbol][0]
                        LOGGER.debug(
                            "DISPOSAL on %s of %s, quantity %d, capital gain $%s",
                            date_from_index(date_index),
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
            portfolio,
            disposal_count,
            round_decimal(disposal_proceeds, 2),
            round_decimal(allowable_costs, 2),
            round_decimal(capital_gain, 2),
            round_decimal(capital_loss, 2),
            Decimal(allowance) if allowance is not None else None,
            calculation_log,
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
        args.schwab, args.trading212, args.mssb
    )
    converter = CurrencyConverter(read_gbp_prices_history(args.gbp_history))
    initial_prices = InitialPrices(read_initial_prices(args.initial_prices))

    calculator = CapitalGainsCalculator(args.year, converter, initial_prices)
    # First pass converts broker transactions to HMRC transactions.
    # This means applying same day rule and collapsing all transactions with
    # same type within the same day.
    # It also converts prices to GBP, validates data and calculates dividends,
    # taxes on dividends and interest.
    acquisition_list, disposal_list = calculator.convert_to_hmrc_transactions(
        broker_transactions
    )
    # Second pass calculates capital gain tax for the given tax year.
    report = calculator.calculate_capital_gain(acquisition_list, disposal_list)
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
