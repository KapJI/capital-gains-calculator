#!/usr/bin/env python3

from collections import defaultdict
import datetime
import decimal
from decimal import Decimal
import sys
from typing import Dict, List, Tuple

from . import render_latex
from .args_parser import create_parser
from .currency_converter import CurrencyConverter
from .dates import date_from_index, date_to_index, internal_start_date, is_date
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
from .misc import round_decimal
from .model import (
    ActionType,
    BrokerTransaction,
    CalculationEntry,
    CalculationLog,
    CapitalGainsReport,
    RuleType,
)
from .parsers import (
    read_broker_transactions,
    read_gbp_prices_history,
    read_initial_prices,
)

# For mapping of dates to int
HmrcTransactionLog = Dict[int, Dict[str, Tuple[Decimal, Decimal, Decimal]]]


def has_key(transactions: HmrcTransactionLog, date_index: int, symbol: str) -> bool:
    return date_index in transactions and symbol in transactions[date_index]


class CapitalGainsCalculator:
    # Allowances
    # https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances#tax-free-allowances-for-capital-gains-tax
    CAPITAL_GAIN_ALLOWANCES: Dict[int, int] = {
        2014: 11000,
        2015: 11100,
        2016: 11100,
        2017: 11300,
        2018: 11700,
        2019: 12000,
        2020: 12300,
    }

    def __init__(
        self, tax_year: int, converter: CurrencyConverter, initial_prices: InitialPrices
    ):
        self.tax_year = tax_year

        # 6 April
        self.tax_year_start_date = datetime.date(tax_year, 4, 6)
        # 5 April
        self.tax_year_end_date = datetime.date(tax_year + 1, 4, 5)

        self.converter = converter
        self.initial_prices = initial_prices

    def date_in_tax_year(self, date: datetime.date) -> bool:
        assert is_date(date)
        return self.tax_year_start_date <= date and date <= self.tax_year_end_date

    @staticmethod
    def add_to_list(
        current_list: HmrcTransactionLog,
        date_index: int,
        symbol: str,
        quantity: Decimal,
        amount: Decimal,
        fees: Decimal,
    ) -> None:
        # assert quantity is not None
        if date_index not in current_list:
            current_list[date_index] = {}
        if symbol not in current_list[date_index]:
            current_list[date_index][symbol] = (Decimal(0), Decimal(0), Decimal(0))
        current_quantity, current_amount, current_fees = current_list[date_index][
            symbol
        ]
        current_list[date_index][symbol] = (
            current_quantity + quantity,
            current_amount + amount,
            current_fees + fees,
        )

    def add_acquisition(
        self,
        portfolio: Dict[str, Decimal],
        acquisition_list: HmrcTransactionLog,
        transaction: BrokerTransaction,
    ) -> None:
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
            amount = quantity * self.initial_prices.get(transaction.date, symbol)
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
        CapitalGainsCalculator.add_to_list(
            acquisition_list,
            date_to_index(transaction.date),
            symbol,
            quantity,
            self.converter.to_gbp_for(amount, transaction),
            self.converter.to_gbp_for(transaction.fees, transaction),
        )

    def add_disposal(
        self,
        portfolio: Dict[str, Decimal],
        disposal_list: HmrcTransactionLog,
        transaction: BrokerTransaction,
    ) -> None:
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
        CapitalGainsCalculator.add_to_list(
            disposal_list,
            date_to_index(transaction.date),
            symbol,
            quantity,
            self.converter.to_gbp_for(amount, transaction),
            self.converter.to_gbp_for(transaction.fees, transaction),
        )

    @staticmethod
    def swift_date(date: datetime.date) -> str:
        return date.strftime("%d/%m/%Y")

    def convert_to_hmrc_transactions(
        self,
        transactions: List[BrokerTransaction],
    ) -> Tuple[HmrcTransactionLog, HmrcTransactionLog]:
        # We keep a balance per broker,currency pair
        balance: Dict[Tuple[str, str], Decimal] = defaultdict(lambda: Decimal(0))
        dividends = Decimal(0)
        dividends_tax = Decimal(0)
        interest = Decimal(0)
        total_sells = Decimal(0)
        portfolio: Dict[str, Decimal] = {}
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
                # TODO: cleanup
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
                CapitalGainsCalculator.add_to_list(
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

    def process_acquisition(
        self,
        acquisition_list: HmrcTransactionLog,
        bed_and_breakfast_list: HmrcTransactionLog,
        portfolio: Dict[str, Tuple[Decimal, Decimal]],
        symbol: str,
        date_index: int,
    ) -> List[CalculationEntry]:
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
        if acquisition_quantity > 0:
            acquisition_price = acquisition_amount / acquisition_quantity
            if has_key(bed_and_breakfast_list, date_index, symbol):
                (
                    bed_and_breakfast_quantity,
                    bed_and_breakfast_amount,
                    bed_and_breakfast_fees,
                ) = bed_and_breakfast_list[date_index][symbol]
                assert bed_and_breakfast_quantity <= acquisition_quantity
                acquisition_amount -= bed_and_breakfast_quantity * acquisition_price
                acquisition_amount += bed_and_breakfast_amount
                assert acquisition_amount > 0
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
                    fees=acquisition_fees,
                    allowable_cost=original_acquisition_amount,
                )
            )
        return calculation_entries

    def process_disposal(
        self,
        acquisition_list: HmrcTransactionLog,
        disposal_list: HmrcTransactionLog,
        bed_and_breakfast_list: HmrcTransactionLog,
        portfolio: Dict[str, Tuple[Decimal, Decimal]],
        symbol: str,
        date_index: int,
    ) -> Tuple[Decimal, List[CalculationEntry]]:
        disposal_quantity, proceeds_amount, disposal_fees = disposal_list[date_index][
            symbol
        ]
        disposal_price = proceeds_amount / disposal_quantity
        current_quantity, current_amount = portfolio[symbol]
        assert disposal_quantity <= current_quantity
        chargeable_gain = Decimal(0)
        calculation_entries = []
        # Same day rule is first
        if has_key(acquisition_list, date_index, symbol):
            same_day_quantity, same_day_amount, same_day_fees = acquisition_list[
                date_index
            ][symbol]
            bed_and_breakfast_quantity = Decimal(0)
            if has_key(bed_and_breakfast_list, date_index, symbol):
                (
                    bed_and_breakfast_quantity,
                    _bb_amount,
                    _bb_fees,
                ) = bed_and_breakfast_list[date_index][symbol]
            assert bed_and_breakfast_quantity <= same_day_quantity
            available_quantity = min(
                disposal_quantity, same_day_quantity - bed_and_breakfast_quantity
            )
            if available_quantity > 0:
                acquisition_price = same_day_amount / same_day_quantity
                same_day_proceeds = available_quantity * disposal_price
                same_day_allowable_cost = available_quantity * acquisition_price
                same_day_gain = same_day_proceeds - same_day_allowable_cost
                chargeable_gain += same_day_gain
                # print(
                #     f"SAME DAY"
                #     f", quantity {available_quantity}"
                #     f", gain {same_day_gain}
                #     f", disposal price {disposal_price}"
                #     f", acquisition price {acquisition_price}"
                # )
                disposal_quantity -= available_quantity
                proceeds_amount -= available_quantity * disposal_price
                current_quantity -= available_quantity
                # These shares shouldn't be added to Section 104 holding
                current_amount -= available_quantity * acquisition_price
                if current_quantity == 0:
                    assert current_amount == 0, f"current amount {current_amount}"
                calculation_entries.append(
                    CalculationEntry(
                        rule_type=RuleType.SAME_DAY,
                        quantity=available_quantity,
                        amount=same_day_proceeds,
                        gain=same_day_gain,
                        allowable_cost=same_day_allowable_cost,
                        fees=same_day_fees,
                        new_quantity=current_quantity,
                        new_pool_cost=current_amount,
                    )
                )

        # Bed and breakfast rule next
        if disposal_quantity > 0:
            for i in range(30):
                search_index = date_index + i + 1
                if has_key(acquisition_list, search_index, symbol):
                    (
                        acquisition_quantity,
                        acquisition_amount,
                        acquisition_fees,
                    ) = acquisition_list[search_index][symbol]
                    bed_and_breakfast_quantity = Decimal(0)
                    if has_key(bed_and_breakfast_list, search_index, symbol):
                        (
                            bed_and_breakfast_quantity,
                            _bb_amount,
                            _bb_fees,
                        ) = bed_and_breakfast_list[search_index][symbol]
                    assert bed_and_breakfast_quantity <= acquisition_quantity
                    # This can be some management fee entry or already used
                    # by bed and breakfast rule
                    if acquisition_quantity - bed_and_breakfast_quantity == 0:
                        continue
                    print(
                        f"WARNING: Bed and breakfasting for {symbol}."
                        f" Disposed on {date_from_index(date_index)}"
                        f" and acquired again on {date_from_index(search_index)}"
                    )
                    available_quantity = min(
                        disposal_quantity,
                        acquisition_quantity - bed_and_breakfast_quantity,
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
                    # print(
                    #     f"BED & BREAKFAST"
                    #     f", quantity {available_quantity}"
                    #     f", gain {bed_and_breakfast_gain}"
                    #     f", disposal price {disposal_price}"
                    #     f", acquisition price {acquisition_price}"
                    # )
                    disposal_quantity -= acquisition_quantity
                    proceeds_amount -= available_quantity * disposal_price
                    current_price = current_amount / current_quantity
                    amount_delta = available_quantity * current_price
                    current_quantity -= available_quantity
                    current_amount -= amount_delta
                    if current_quantity == 0:
                        assert current_amount == 0, f"current amount {current_amount}"
                    CapitalGainsCalculator.add_to_list(
                        bed_and_breakfast_list,
                        search_index,
                        symbol,
                        available_quantity,
                        amount_delta,
                        Decimal(0),
                    )
                    calculation_entries.append(
                        CalculationEntry(
                            rule_type=RuleType.BED_AND_BREAKFAST,
                            quantity=available_quantity,
                            amount=bed_and_breakfast_proceeds,
                            gain=bed_and_breakfast_gain,
                            allowable_cost=bed_and_breakfast_allowable_cost,
                            # TODO: support fees
                            fees=acquisition_fees,
                            bed_and_breakfast_date_index=search_index,
                            new_quantity=current_quantity,
                            new_pool_cost=current_amount,
                        )
                    )
        if disposal_quantity > 0:
            allowable_cost = (
                current_amount * Decimal(disposal_quantity) / Decimal(current_quantity)
            )
            chargeable_gain += proceeds_amount - allowable_cost
            # print(
            #     f"SECTION 104"
            #     f", quantity {disposal_quantity}"
            #     f", gain {proceeds_amount - allowable_cost}"
            #     f", proceeds amount {proceeds_amount}"
            #     f", allowable cost {allowable_cost}"
            # )
            current_quantity -= disposal_quantity
            current_amount -= allowable_cost
            if current_quantity == 0:
                assert (
                    round_decimal(current_amount, 10) == 0
                ), f"current amount {current_amount}"
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
                    quantity=disposal_quantity,
                    amount=proceeds_amount,
                    gain=proceeds_amount - allowable_cost,
                    allowable_cost=allowable_cost,
                    # CHECK THIS!
                    fees=disposal_fees,
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
        begin_index = date_to_index(internal_start_date)
        tax_year_start_index = date_to_index(self.tax_year_start_date)
        end_index = date_to_index(self.tax_year_end_date)
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        bed_and_breakfast_list: HmrcTransactionLog = {}
        portfolio: Dict[str, Tuple[Decimal, Decimal]] = {}
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
                        # print(
                        #     f"DISPOSAL on {date_from_index(date_index)} of {symbol}"
                        #     f", quantity {transaction_quantity}: "
                        #     f"capital gain: ${round_decimal(transaction_capital_gain, 2)}"
                        # )
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
        allowance = self.CAPITAL_GAIN_ALLOWANCES.get(self.tax_year)
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
    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True
    args = create_parser().parse_args()

    # Read data from input files
    broker_transactions = read_broker_transactions(args.schwab, args.trading212)
    converter = CurrencyConverter(read_gbp_prices_history(args.gbp_history))
    initial_prices = InitialPrices(read_initial_prices(args.initial_prices))

    calculator = CapitalGainsCalculator(args.tax_year, converter, initial_prices)
    # First pass converts broker transactions to HMRC transactions.
    # This means applying same day rule and collapsing all transactions with
    # same type in the same day.
    # It also converts prices to GBP, validates data and calculates dividends,
    # taxes on dividends and interest.
    acquisition_list, disposal_list = calculator.convert_to_hmrc_transactions(
        broker_transactions
    )
    # Second pass calculates capital gain tax for the given tax year
    report = calculator.calculate_capital_gain(acquisition_list, disposal_list)
    print(report)
    if args.report:
        render_latex.render_calculations(
            report.calculation_log,
            tax_year=report.tax_year,
            date_from_index=date_from_index,
            output_file=args.report,
        )
    print("All done!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
