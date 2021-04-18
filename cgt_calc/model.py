#!/usr/bin/env python3

import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .dates import DateIndex
from .misc import round_decimal


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


class BrokerTransaction:
    def __init__(
        self,
        date: datetime.date,
        action: ActionType,
        symbol: Optional[str],
        description: str,
        quantity: Optional[Decimal],
        price: Optional[Decimal],
        fees: Decimal,
        amount: Optional[Decimal],
        currency: str,
        broker: str,
    ):
        self.date = date
        self.action = action
        self.symbol = symbol
        self.description = description
        self.quantity = quantity
        self.price = price
        self.fees = fees
        self.amount = amount
        self.currency = currency
        self.broker = broker

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
        if self.currency:
            result += f", currency: {self.currency}"
        if self.broker:
            result += f", broker: {self.broker}"
        return result


class RuleType(Enum):
    SECTION_104 = 1
    SAME_DAY = 2
    BED_AND_BREAKFAST = 3


class CalculationEntry:
    def __init__(
        self,
        rule_type: RuleType,
        quantity: Decimal,
        amount: Decimal,
        fees: Decimal,
        new_quantity: Decimal,
        new_pool_cost: Decimal,
        gain: Optional[Decimal] = None,
        allowable_cost: Optional[Decimal] = None,
        bed_and_breakfast_date_index: int = 0,
    ):
        self.rule_type = rule_type
        self.quantity = quantity
        self.amount = amount
        self.allowable_cost = (
            allowable_cost if allowable_cost is not None else Decimal(0)
        )
        self.fees = fees
        self.gain = gain if gain is not None else Decimal(0)
        self.new_quantity = new_quantity
        self.new_pool_cost = new_pool_cost
        self.bed_and_breakfast_date_index = bed_and_breakfast_date_index
        if self.amount >= 0:
            assert self.gain == self.amount - self.allowable_cost

    def __str__(self) -> str:
        return (
            f"{self.rule_type.name.replace('_', ' ')}, "
            f"quantity: {self.quantity}, "
            f"disposal proceeds: {self.amount}, "
            f"allowable cost: {self.allowable_cost}, "
            f"fees: {self.fees}, "
            f"gain: {self.gain}"
        )


CalculationLog = Dict[DateIndex, Dict[str, List[CalculationEntry]]]


class CapitalGainsReport:
    def __init__(
        self,
        tax_year: int,
        portfolio: Dict[str, Tuple[Decimal, Decimal]],
        disposal_count: int,
        disposal_proceeds: Decimal,
        allowable_costs: Decimal,
        capital_gain: Decimal,
        capital_loss: Decimal,
        capital_gain_allowance: Optional[Decimal],
        calculation_log: CalculationLog,
    ):
        self.tax_year = tax_year
        self.portfolio = portfolio
        self.disposal_count = disposal_count
        self.disposal_proceeds = disposal_proceeds
        self.allowable_costs = allowable_costs
        self.capital_gain = capital_gain
        self.capital_loss = capital_loss
        self.capital_gain_allowance = capital_gain_allowance
        self.calculation_log = calculation_log

    def total_gain(self):
        return self.capital_gain + self.capital_loss

    def taxable_gain(self):
        assert self.capital_gain_allowance is not None
        return max(Decimal(0), self.total_gain() - self.capital_gain_allowance)

    def __str__(self):
        s = f"Portfolio at the end of {self.tax_year}/{self.tax_year + 1} tax year:\n"
        for symbol, (quantity, amount) in self.portfolio.items():
            if quantity > 0:
                s += f"  {symbol}: {round_decimal(quantity, 2)}, £{round_decimal(amount, 2)}\n"
        s += f"For tax year {self.tax_year}/{self.tax_year + 1}:\n"
        s += f"Number of disposals: {self.disposal_count}\n"
        s += f"Disposal proceeds: £{self.disposal_proceeds}\n"
        s += f"Allowable costs: £{self.allowable_costs}\n"
        s += f"Capital gain: £{self.capital_gain}\n"
        s += f"Capital loss: £{-self.capital_loss}\n"
        s += f"Total capital gain: £{self.total_gain()}\n"
        if self.capital_gain_allowance is not None:
            s += f"Taxable capital gain: £{self.taxable_gain()}\n"
        else:
            s += "WARNING: Missing allowance for this tax year\n"
        return s
