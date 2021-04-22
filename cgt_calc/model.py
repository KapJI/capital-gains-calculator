"""Model classes."""
from __future__ import annotations

from dataclasses import dataclass
import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Tuple

from .util import round_decimal

# Number of days from some unspecified fixed date
DateIndex = int

# For mapping of dates to int
HmrcTransactionLog = Dict[int, Dict[str, Tuple[Decimal, Decimal, Decimal]]]


class ActionType(Enum):
    """Type of transaction action."""

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


@dataclass
class BrokerTransaction:
    """Broken transaction data."""

    date: datetime.date
    action: ActionType
    symbol: str | None
    description: str
    quantity: Decimal | None
    price: Decimal | None
    fees: Decimal
    amount: Decimal | None
    currency: str
    broker: str


class RuleType(Enum):
    """HMRC rule type."""

    SECTION_104 = 1
    SAME_DAY = 2
    BED_AND_BREAKFAST = 3


class CalculationEntry:
    """Calculation entry for final report."""

    def __init__(
        self,
        rule_type: RuleType,
        quantity: Decimal,
        amount: Decimal,
        fees: Decimal,
        new_quantity: Decimal,
        new_pool_cost: Decimal,
        gain: Decimal | None = None,
        allowable_cost: Decimal | None = None,
        bed_and_breakfast_date_index: int = 0,
    ):
        """Create calculation entry."""
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
        """Return string representation."""
        return (
            f"{self.rule_type.name.replace('_', ' ')}, "
            f"quantity: {self.quantity}, "
            f"disposal proceeds: {self.amount}, "
            f"allowable cost: {self.allowable_cost}, "
            f"fees: {self.fees}, "
            f"gain: {self.gain}"
        )


CalculationLog = Dict[DateIndex, Dict[str, List[CalculationEntry]]]


@dataclass
class CapitalGainsReport:
    """Store calculated report."""

    tax_year: int
    portfolio: dict[str, tuple[Decimal, Decimal]]
    disposal_count: int
    disposal_proceeds: Decimal
    allowable_costs: Decimal
    capital_gain: Decimal
    capital_loss: Decimal
    capital_gain_allowance: Decimal | None
    calculation_log: CalculationLog

    def total_gain(self) -> Decimal:
        """Total capital gain."""
        return self.capital_gain + self.capital_loss

    def taxable_gain(self) -> Decimal:
        """Taxable gain with current allowance."""
        assert self.capital_gain_allowance is not None
        return max(Decimal(0), self.total_gain() - self.capital_gain_allowance)

    def __str__(self) -> str:
        """Return string representation."""
        out = f"Portfolio at the end of {self.tax_year}/{self.tax_year + 1} tax year:\n"
        for symbol, (quantity, amount) in self.portfolio.items():
            if quantity > 0:
                out += (
                    f"  {symbol}: {round_decimal(quantity, 2)}, "
                    f"£{round_decimal(amount, 2)}\n"
                )
        out += f"For tax year {self.tax_year}/{self.tax_year + 1}:\n"
        out += f"Number of disposals: {self.disposal_count}\n"
        out += f"Disposal proceeds: £{self.disposal_proceeds}\n"
        out += f"Allowable costs: £{self.allowable_costs}\n"
        out += f"Capital gain: £{self.capital_gain}\n"
        out += f"Capital loss: £{-self.capital_loss}\n"
        out += f"Total capital gain: £{self.total_gain()}\n"
        if self.capital_gain_allowance is not None:
            out += f"Taxable capital gain: £{self.taxable_gain()}\n"
        else:
            out += "WARNING: Missing allowance for this tax year\n"
        return out
