#!/usr/bin/env python3

import csv
import datetime
import decimal
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Tuple

import jinja2

# First year of tax year
tax_year = 2019
# Allowance is £12000 for 2019/20
# https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances#tax-free-allowances-for-capital-gains-tax
capital_gain_allowance = 12000
# Schwab transactions
transactions_file = "transactions.csv"
# Montly GBP/USD history from
# https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat
gbp_history_file = "GBP_USD_monthly_history.csv"
# Initial vesting and spin-off prices
initial_prices_file = "initial_prices.csv"
# Latex template for calculations report
calculations_template_file = "template.tex"

# 6 April
tax_year_start_date = datetime.date(tax_year, 4, 6)
# 5 April
tax_year_end_date = datetime.date(tax_year + 1, 4, 5)
# For mapping of dates to int
internal_start_date = datetime.date(2010, 1, 1)
HmrcTransactionLog = Dict[int, Dict[str, Tuple[int, Decimal, Decimal]]]

gbp_history: Dict[int, Decimal] = {}
fb_history: Dict[int, Decimal] = {}
initial_prices: Dict[int, Dict[str, Decimal]] = {}


class BrokerTransaction:
    def __init__(self, row: List[str]):
        assert len(row) == 9
        assert row[8] == "", "should be empty"
        as_of_str = " as of "
        if as_of_str in row[0]:
            index = row[0].find(as_of_str) + len(as_of_str)
            date_str = row[0][index:]
        else:
            date_str = row[0]
        self.date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        self.action = row[1]
        self.symbol = row[2]
        self.description = row[3]
        self.quantity = int(row[4]) if row[4] != "" else None
        self.price = Decimal(row[5].replace("$", "")) if row[5] != "" else None
        self.fees = Decimal(row[6].replace("$", "")) if row[6] != "" else Decimal(0)
        self.amount = Decimal(row[7].replace("$", "")) if row[7] != "" else None

    def __str__(self) -> str:
        result = f'date: {self.date}, action: "{self.action}"'
        if self.symbol:
            result += f", symbol: {self.symbol}"
        if self.description:
            result += f', description: "{self.description}"'
        if self.quantity:
            result += f", quantity: {self.quantity}"
        if self.price:
            result += f", price: {self.price}"
        if self.fees:
            result += f", fees: {self.fees}"
        if self.amount:
            result += f", amount: {self.amount}"
        return result


class ActionType(Enum):
    BUY = 1
    SELL = 2
    TRANSFER = 3
    STOCK_ACTIVITY = 4
    DIVIDEND = 5
    TAX = 6
    FEE = 7
    ADJUSTMENT = 8
    CAPITAL_GAIN = 9
    SPIN_OFF = 10
    INTEREST = 11

    @staticmethod
    def from_str(label: str):
        if label == "Buy":
            return ActionType.BUY
        elif label == "Sell":
            return ActionType.SELL
        elif label in [
            "MoneyLink Transfer",
            "Misc Cash Entry",
            "Service Fee",
            "Wire Funds",
            "Funds Received",
            "Journal",
            "Cash In Lieu",
        ]:
            return ActionType.TRANSFER
        elif label == "Stock Plan Activity":
            return ActionType.STOCK_ACTIVITY
        elif label in ["Qualified Dividend", "Cash Dividend"]:
            return ActionType.DIVIDEND
        elif label in ["NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"]:
            return ActionType.TAX
        elif label == "ADR Mgmt Fee":
            return ActionType.FEE
        elif label in ["Adjustment", "IRS Withhold Adj"]:
            return ActionType.ADJUSTMENT
        elif label in ["Short Term Cap Gain", "Long Term Cap Gain"]:
            return ActionType.CAPITAL_GAIN
        elif label == "Spin-off":
            return ActionType.SPIN_OFF
        elif label == "Credit Interest":
            return ActionType.INTEREST
        else:
            raise Exception(f"Unknown action: {label}")


class InitialPricesEntry:
    def __init__(self, row: List[str]):
        assert len(row) == 3
        # date,symbol,price
        self.date = self._parse_date(row[0])
        self.symbol = row[1]
        self.price = Decimal(row[2])

    @staticmethod
    def _parse_date(date_str: str) -> datetime.date:
        return datetime.datetime.strptime(date_str, "%b %d, %Y").date()

    def __str__(self) -> str:
        return f"date: {self.date}, symbol: {self.symbol}, price: {self.price}"


class RuleType(Enum):
    SECTION_104 = 1
    SAME_DAY = 2
    BED_AND_BREAKFAST = 3


class CalculationEntry:
    def __init__(
        self,
        rule_type: RuleType,
        quantity: int,
        amount: Decimal,
        fees: Decimal,
        new_quantity: int,
        new_pool_cost: Decimal,
        gain: Decimal = Decimal(0),
        allowable_cost: Decimal = Decimal(0),
        bed_and_breakfast_date_index: int = 0,
    ):
        self.rule_type = rule_type
        self.quantity = quantity
        self.amount = amount
        self.allowable_cost = allowable_cost
        self.fees = fees
        self.gain = gain
        self.new_quantity = new_quantity
        self.new_pool_cost = new_pool_cost
        self.bed_and_breakfast_date_index = bed_and_breakfast_date_index
        if amount >= 0:
            assert gain == amount - allowable_cost

    def __str__(self) -> str:
        return (
            f"{self.rule_type.name.replace('_', ' ')}, "
            f"quantity: {self.quantity}, "
            f"disposal proceeds: {self.amount}, "
            f"allowable cost: {self.allowable_cost}, "
            f"fees: {self.fees}, "
            f"gain: {self.gain}"
        )


CalculationLog = Dict[int, Dict[str, List[CalculationEntry]]]


def gbp_price(date: datetime.date) -> Decimal:
    assert is_date(date)
    # Set day to 1 to get monthly price
    index = date_to_index(date.replace(day=1))
    if index in gbp_history:
        return gbp_history[index]
    else:
        raise Exception(f"No GBP/USD price for {date}")


def get_initial_price(date: datetime.date, symbol: str) -> Decimal:
    assert is_date(date)
    date_index = date_to_index(date)
    if date_index in initial_prices and symbol in initial_prices[date_index]:
        return initial_prices[date_index][symbol]
    else:
        raise Exception(f"No {symbol} price for {date}")


def round_decimal(value: Decimal, digits: int = 0) -> Decimal:
    with decimal.localcontext() as ctx:
        ctx.rounding = decimal.ROUND_HALF_UP
        return Decimal(round(value, digits))


def convert_to_gbp(amount: Decimal, date: datetime.date) -> Decimal:
    return amount / gbp_price(date)


def date_to_index(date: datetime.date) -> int:
    assert is_date(date)
    return (date - internal_start_date).days


def date_from_index(date_index: int) -> datetime.date:
    return internal_start_date + datetime.timedelta(days=date_index)


def is_date(date: datetime.date) -> bool:
    if isinstance(date, datetime.date) and not isinstance(date, datetime.datetime):
        return True
    else:
        raise Exception(f'should be datetime.date: {type(date)} "{date}"')


def date_in_tax_year(date: datetime.date) -> bool:
    assert is_date(date)
    return tax_year_start_date <= date and date <= tax_year_end_date


def has_key(transactions: HmrcTransactionLog, date_index: int, symbol: str) -> bool:
    return date_index in transactions and symbol in transactions[date_index]


def add_to_list(
    current_list: HmrcTransactionLog,
    date_index: int,
    symbol: str,
    quantity: int,
    amount: Decimal,
    fees: Decimal,
) -> None:
    # assert quantity is not None
    if date_index not in current_list:
        current_list[date_index] = {}
    if symbol not in current_list[date_index]:
        current_list[date_index][symbol] = (0, Decimal(0), Decimal(0))
    current_quantity, current_amount, current_fees = current_list[date_index][symbol]
    current_list[date_index][symbol] = (
        current_quantity + quantity,
        current_amount + amount,
        current_fees + fees,
    )


def read_broker_transactions() -> List[BrokerTransaction]:
    with open(transactions_file) as csv_file:
        lines = [line for line in csv.reader(csv_file)]
        lines = lines[2:-1]
        transactions = [BrokerTransaction(row) for row in lines]
        transactions.reverse()
        return transactions


def read_gbp_prices_history() -> None:
    with open(gbp_history_file) as csv_file:
        lines = [line for line in csv.reader(csv_file)]
        lines = lines[1:]
        for row in lines:
            assert len(row) == 2
            price_date = datetime.datetime.strptime(row[0], "%m/%Y").date()
            gbp_history[date_to_index(price_date)] = Decimal(row[1])


def read_initial_prices() -> None:
    with open(initial_prices_file) as csv_file:
        lines = [line for line in csv.reader(csv_file)]
        lines = lines[1:]
        for row in lines:
            entry = InitialPricesEntry(row)
            date_index = date_to_index(entry.date)
            if date_index not in initial_prices:
                initial_prices[date_index] = {}
            initial_prices[date_index][entry.symbol] = entry.price


def add_acquisition(
    portfolio: Dict[str, int],
    acquisition_list: HmrcTransactionLog,
    transaction: BrokerTransaction,
) -> None:
    symbol = transaction.symbol
    quantity = transaction.quantity
    assert symbol != ""
    assert quantity is not None
    assert quantity > 0
    # This is basically only for data validation
    if symbol in portfolio:
        portfolio[symbol] += quantity
    else:
        portfolio[symbol] = quantity
    # Add to acquisition_list to apply same day rule
    action_type = ActionType.from_str(transaction.action)
    if action_type in [ActionType.STOCK_ACTIVITY, ActionType.SPIN_OFF]:
        amount = quantity * get_initial_price(transaction.date, symbol)
    else:
        assert transaction.amount is not None
        assert transaction.price is not None
        calculated_amount = round_decimal(
            quantity * transaction.price + transaction.fees, 2
        )
        amount = -transaction.amount
        assert calculated_amount == amount, f"{calculated_amount} != {amount}"
    add_to_list(
        acquisition_list,
        date_to_index(transaction.date),
        symbol,
        quantity,
        convert_to_gbp(amount, transaction.date),
        convert_to_gbp(transaction.fees, transaction.date),
    )


def add_disposal(
    portfolio: Dict[str, int],
    disposal_list: HmrcTransactionLog,
    transaction: BrokerTransaction,
) -> None:
    symbol = transaction.symbol
    quantity = transaction.quantity
    assert symbol != ""
    assert symbol in portfolio, "reversed order?"
    assert quantity is not None
    assert quantity > 0
    assert portfolio[symbol] >= quantity
    # This is basically only for data validation
    portfolio[symbol] -= quantity
    if portfolio[symbol] == 0:
        del portfolio[symbol]
    # Add to disposal_list to apply same day rule
    assert transaction.amount is not None
    assert transaction.price is not None
    amount = transaction.amount
    calculated_amount = round_decimal(
        quantity * transaction.price - transaction.fees, 2
    )
    assert calculated_amount == amount, f"{calculated_amount} != {amount}"
    add_to_list(
        disposal_list,
        date_to_index(transaction.date),
        symbol,
        quantity,
        convert_to_gbp(amount, transaction.date),
        convert_to_gbp(transaction.fees, transaction.date),
    )


def swift_date(date: datetime.date) -> str:
    return date.strftime("%d/%m/%Y")


def convert_to_hmrc_transactions(
    transactions: List[BrokerTransaction],
) -> Tuple[HmrcTransactionLog, HmrcTransactionLog]:
    balance = Decimal(0)
    dividends = Decimal(0)
    dividends_tax = Decimal(0)
    interest = Decimal(0)
    total_sells = Decimal(0)
    portfolio: Dict[str, int] = {}
    acquisition_list: HmrcTransactionLog = {}
    disposal_list: HmrcTransactionLog = {}

    for transaction in transactions:
        assert balance >= 0, "balance can't be negative"
        action_type = ActionType.from_str(transaction.action)
        if action_type is ActionType.TRANSFER:
            assert transaction.amount is not None
            balance += transaction.amount
        elif action_type is ActionType.BUY:
            assert transaction.amount is not None
            balance += transaction.amount
            add_acquisition(portfolio, acquisition_list, transaction)
        elif action_type is ActionType.SELL:
            assert transaction.amount is not None
            balance += transaction.amount
            add_disposal(portfolio, disposal_list, transaction)
            # TODO: cleanup
            if date_in_tax_year(transaction.date):
                total_sells += convert_to_gbp(transaction.amount, transaction.date)
        elif action_type is ActionType.FEE:
            assert transaction.amount is not None
            balance += transaction.amount
            transaction.fees = -transaction.amount
            transaction.quantity = 0
            gbp_fees = convert_to_gbp(transaction.fees, transaction.date)
            add_to_list(
                acquisition_list,
                date_to_index(transaction.date),
                transaction.symbol,
                transaction.quantity,
                gbp_fees,
                gbp_fees,
            )
        elif action_type in [ActionType.STOCK_ACTIVITY, ActionType.SPIN_OFF]:
            add_acquisition(portfolio, acquisition_list, transaction)
        elif action_type in [ActionType.DIVIDEND, ActionType.CAPITAL_GAIN]:
            assert transaction.amount is not None
            balance += transaction.amount
            if date_in_tax_year(transaction.date):
                dividends += convert_to_gbp(transaction.amount, transaction.date)
        elif action_type in [ActionType.TAX, ActionType.ADJUSTMENT]:
            assert transaction.amount is not None
            balance += transaction.amount
            if date_in_tax_year(transaction.date):
                dividends_tax += convert_to_gbp(transaction.amount, transaction.date)
        elif action_type is ActionType.INTEREST:
            assert transaction.amount is not None
            balance += transaction.amount
            if date_in_tax_year(transaction.date):
                interest += convert_to_gbp(transaction.amount, transaction.date)
        else:
            raise Exception(f"Action not processed: {action_type}")
    print("First pass completed")
    print("Final portfolio:")
    for stock, quantity in portfolio.items():
        print(f"  {stock}: {quantity}")
    print(f"Final balance: ${balance}")
    print(f"Dividends: £{round_decimal(dividends, 2)}")
    print(f"Dividend taxes: £{round_decimal(-dividends_tax, 2)}")
    print(f"Interest: £{round_decimal(interest, 2)}")
    print(f"Disposal proceeds: £{round_decimal(total_sells, 2)}")
    print("")
    return acquisition_list, disposal_list


def process_acquisition(
    acquisition_list: HmrcTransactionLog,
    bed_and_breakfast_list: HmrcTransactionLog,
    portfolio: Dict[str, Tuple[int, Decimal]],
    symbol: str,
    date_index: int,
) -> List[CalculationEntry]:
    acquisition_quantity, acquisition_amount, acquisition_fees = acquisition_list[
        date_index
    ][symbol]
    original_acquisition_amount = acquisition_amount
    if symbol not in portfolio:
        portfolio[symbol] = (0, Decimal(0))
    current_quantity, current_amount = portfolio[symbol]
    calculation_entries = []

    # Management fee transaction can have 0 quantity
    assert acquisition_quantity >= 0
    assert acquisition_amount > 0
    bed_and_breakfast_quantity = 0
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
    acquisition_list: HmrcTransactionLog,
    disposal_list: HmrcTransactionLog,
    bed_and_breakfast_list: HmrcTransactionLog,
    portfolio: Dict[str, Tuple[int, Decimal]],
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
        bed_and_breakfast_quantity = 0
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
                bed_and_breakfast_quantity = 0
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
                    disposal_quantity, acquisition_quantity - bed_and_breakfast_quantity
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
                add_to_list(
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
    acquisition_list: HmrcTransactionLog, disposal_list: HmrcTransactionLog,
) -> CalculationLog:
    begin_index = date_to_index(internal_start_date)
    tax_year_start_index = date_to_index(tax_year_start_date)
    end_index = date_to_index(tax_year_end_date)
    disposal_count = 0
    disposal_proceeds = Decimal(0)
    allowable_costs = Decimal(0)
    capital_gain = Decimal(0)
    capital_loss = Decimal(0)
    bed_and_breakfast_list: HmrcTransactionLog = {}
    portfolio: Dict[str, Tuple[int, Decimal]] = {}
    calculation_log: CalculationLog = {}
    for date_index in range(begin_index, end_index + 1):
        if date_index in acquisition_list:
            for symbol in acquisition_list[date_index]:
                calculation_entries = process_acquisition(
                    acquisition_list,
                    bed_and_breakfast_list,
                    portfolio,
                    symbol,
                    date_index,
                )
                if date_index >= tax_year_start_index:
                    if date_index not in calculation_log:
                        calculation_log[date_index] = {}
                    calculation_log[date_index][f"buy${symbol}"] = calculation_entries
        if date_index in disposal_list:
            for symbol in disposal_list[date_index]:
                transaction_capital_gain, calculation_entries = process_disposal(
                    acquisition_list,
                    disposal_list,
                    bed_and_breakfast_list,
                    portfolio,
                    symbol,
                    date_index,
                )
                if date_index >= tax_year_start_index:
                    disposal_count += 1
                    transaction_disposal_proceeds = disposal_list[date_index][symbol][1]
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
                    calculated_quantity = 0
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
                    assert transaction_capital_gain == round_decimal(calculated_gain, 2)
                    if date_index not in calculation_log:
                        calculation_log[date_index] = {}
                    calculation_log[date_index][f"sell${symbol}"] = calculation_entries
                    if transaction_capital_gain > 0:
                        capital_gain += transaction_capital_gain
                    else:
                        capital_loss += transaction_capital_gain
    print("\nSecond pass completed")
    print(f"Portfolio at the end of {tax_year}/{tax_year + 1} tax year:")
    for symbol in portfolio:
        quantity, amount = portfolio[symbol]
        if quantity > 0:
            print(f"  {symbol}: {quantity}, £{round_decimal(amount, 2)}")
    disposal_proceeds = round_decimal(disposal_proceeds, 2)
    allowable_costs = round_decimal(allowable_costs, 2)
    capital_gain = round_decimal(capital_gain, 2)
    capital_loss = round_decimal(capital_loss, 2)
    print(f"For tax year {tax_year}/{tax_year + 1}:")
    print(f"Number of disposals: {disposal_count}")
    print(f"Disposal proceeds: £{disposal_proceeds}")
    print(f"Allowable costs: £{allowable_costs}")
    print(f"Capital gain: £{capital_gain}")
    print(f"Capital loss: £{-capital_loss}")
    print(f"Total capital gain: £{capital_gain + capital_loss}")
    print(
        f"Taxable capital gain: £{max(Decimal(0), capital_gain + capital_loss - capital_gain_allowance)}"
    )
    print("")
    return calculation_log


def render_calculations(calculation_log: CalculationLog) -> None:
    print("Generate calculations report")
    current_directory = os.path.abspath(".")
    latex_template_env = jinja2.Environment(
        block_start_string="\\BLOCK{",
        block_end_string="}",
        variable_start_string="\\VAR{",
        variable_end_string="}",
        comment_start_string="\\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        autoescape=False,
        loader=jinja2.FileSystemLoader(current_directory),
    )
    template = latex_template_env.get_template(calculations_template_file)
    output_text = template.render(
        calculation_log=calculation_log,
        tax_year=tax_year,
        date_from_index=date_from_index,
        round_decimal=round_decimal,
        Decimal=Decimal,
    )
    generated_file_fd, generated_file = tempfile.mkstemp(suffix=".tex")
    os.write(generated_file_fd, output_text.encode())
    os.close(generated_file_fd)
    output_filename = "calculations"
    subprocess.run(
        [
            "pdflatex",
            f"-output-directory={current_directory}",
            f"-jobname={output_filename}",
            "-interaction=batchmode",
            generated_file,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    os.remove(generated_file)
    os.remove(f"{output_filename}.log")
    os.remove(f"{output_filename}.aux")


def main() -> int:
    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True
    # Read data from input files
    broker_transactions = read_broker_transactions()
    read_gbp_prices_history()
    read_initial_prices()
    # First pass converts broker transactions to HMRC transactions.
    # This means applying same day rule and collapsing all transactions with
    # same type in the same day.
    # It also converts prices to GBP, validates data and calculates dividends,
    # taxes on dividends and interest.
    acquisition_list, disposal_list = convert_to_hmrc_transactions(broker_transactions)
    # Second pass calculates capital gain tax for the given tax year
    calculation_log = calculate_capital_gain(acquisition_list, disposal_list)
    render_calculations(calculation_log)
    print("All done!")

    return 0


sys.exit(main())
