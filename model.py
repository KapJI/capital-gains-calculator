#!/usr/bin/env python3

import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

from dates import DateIndex


class BrokerTransaction:
    def __init__(
        self,
        date: datetime.date,
        action: str,
        symbol: str,
        description: str,
        quantity: Optional[Decimal],
        price: Optional[Decimal],
        fees: Decimal,
        amount: Optional[Decimal],
    ):
        self.date = date
        self.action = action
        self.symbol = symbol
        self.description = description
        self.quantity = quantity
        self.price = price
        self.fees = fees
        self.amount = amount

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


CalculationLog = Dict[DateIndex, Dict[str, List[CalculationEntry]]]
