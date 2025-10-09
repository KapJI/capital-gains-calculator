"""Model classes."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING

from .util import approx_equal, is_currency, round_decimal

if TYPE_CHECKING:
    from collections.abc import Generator


@dataclass
class SpinOff:
    """Class representing spin-off event on a share."""

    # Cost proportion to be applied to the cost of original shares from which
    # Spin-off originated
    cost_proportion: Decimal
    # Source of the Spin-off, e.g MMM for SOLV
    source: str
    # Dest ticker to which SpinOff happened, e.g. SOLV for MMM
    dest: str
    # When the spin-off happened
    date: datetime.date


@dataclass
class TaxTreaty:
    """Class representing a treaty between UK and different countries."""

    country: str
    country_rate: Decimal
    treaty_rate: Decimal


@dataclass
class ExcessReportedIncome:
    """Class representing Excess Reported Income on a fund.

    The income is reported on a fund at the end of its reporting period.
    The income represent an increase of the cost basis at that date and a
    taxable event at the distribution date.
    """

    price: Decimal
    symbol: str
    date: datetime.date
    distribution_date: datetime.date
    is_interest: bool


@dataclass
class ExcessReportedIncomeDistribution:
    """Class representing Excess Reported Income distribution event on a fund.

    This is when the income is distributed to you for tax purposes.
    """

    price: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    quantity: Decimal = Decimal(0)

    def __add__(
        self, transaction: ExcessReportedIncomeDistribution
    ) -> ExcessReportedIncomeDistribution:
        """Add two tax transactions."""
        return self.__class__(
            price=transaction.price,
            amount=self.amount + transaction.amount,
            quantity=self.quantity + transaction.quantity,
        )


@dataclass
class HmrcTransactionData:
    """Hmrc transaction figures."""

    quantity: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    # This is a list to support Bed and Breakfast acquisitions that can cross multiple
    # ERI reports for the same fund. This can happen for example when a fund is
    # liquidated close after its usual reporting data, requiring a new final reporting.
    eris: list[ExcessReportedIncome] = field(default_factory=list)

    def __add__(self, transaction: HmrcTransactionData) -> HmrcTransactionData:
        """Add two transactions."""
        return self.__class__(
            self.quantity + transaction.quantity,
            self.amount + transaction.amount,
            self.fees + transaction.fees,
            self.eris + transaction.eris,
        )


@dataclass
class ForeignCurrencyAmount:
    """Represent a decimal amount in foreign currency."""

    amount: Decimal = Decimal(0)
    currency: str = ""

    def __add__(self, amount: ForeignCurrencyAmount) -> ForeignCurrencyAmount:
        """Add two amounts."""
        assert self.currency or not self.amount, (
            f"Invalid foreign currency amount {self}"
        )
        assert amount.currency or not amount.amount, (
            f"Invalid foreign currency amount {amount}"
        )
        assert (
            not self.currency or not amount.currency or self.currency == amount.currency
        ), f"Incompatible currency operation {self.currency} vs {amount.currency}"
        result = ForeignCurrencyAmount(
            amount=self.amount + amount.amount,
            currency=self.currency or amount.currency,
        )
        assert result.currency or not result.amount, (
            f"Invalid foreign currency result {result}"
        )
        return result


HmrcTransactionLog = dict[datetime.date, dict[str, HmrcTransactionData]]
ForeignAmountLog = dict[tuple[str, datetime.date], ForeignCurrencyAmount]
ExcessReportedIncomeLog = dict[datetime.date, dict[str, ExcessReportedIncome]]
ExcessReportedIncomeDistributionLog = dict[
    datetime.date, dict[str, ExcessReportedIncomeDistribution]
]


class ActionType(Enum):
    """Type of transaction action."""

    BUY = 1
    SELL = 2
    TRANSFER = 3
    STOCK_ACTIVITY = 4
    DIVIDEND = 5
    DIVIDEND_TAX = 6
    FEE = 7
    ADJUSTMENT = 8
    CAPITAL_GAIN = 9
    SPIN_OFF = 10
    INTEREST = 11
    REINVEST_SHARES = 12
    REINVEST_DIVIDENDS = 13
    WIRE_FUNDS_RECEIVED = 14
    STOCK_SPLIT = 15
    CASH_MERGER = 16
    EXCESS_REPORTED_INCOME = 17


class CalcuationType(Enum):
    """Calculation type enumeration."""

    ACQUISITION = 1
    DISPOSAL = 2


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
    isin: str | None = None

    def __post_init__(self) -> None:
        """Validate BrokerTransaction data."""
        assert is_currency(self.currency), (
            f"Invalid Currency {self.currency} for transaction {self}"
        )


class RuleType(Enum):
    """HMRC rule type."""

    SECTION_104 = 1
    SAME_DAY = 2
    BED_AND_BREAKFAST = 3
    SPIN_OFF = 4
    DIVIDEND = 5
    INTEREST = 6
    EXCESS_REPORTED_INCOME = 7
    EXCESS_REPORTED_INCOME_DISTRIBUTION = 8


@dataclass
class Dividend:
    """Class representing a dividend event."""

    date: datetime.date
    symbol: str
    amount: Decimal
    tax_at_source: Decimal
    is_interest: bool
    tax_treaty: TaxTreaty | None

    @property
    def tax_treaty_amount(self) -> Decimal:
        """As title."""
        if self.tax_treaty is None:
            return Decimal(0)
        return self.amount * self.tax_treaty.treaty_rate


class CalculationEntry:  # noqa: SIM119 # this has non-trivial constructor
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
        bed_and_breakfast_date_index: datetime.date | None = None,
        spin_off: SpinOff | None = None,
        dividend: Dividend | None = None,
        eris: list[ExcessReportedIncome] | None = None,
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
        self.spin_off = spin_off
        self.dividend = dividend
        self.eris = eris or []
        if self.rule_type == RuleType.EXCESS_REPORTED_INCOME:
            assert self.allowable_cost > 0, str(self)
            assert approx_equal(
                self.allowable_cost, self.amount + self.new_pool_cost
            ), f"Mismatch: {self.allowable_cost} != "
            f"{self.amount} + {self.new_pool_cost} (for {self})"
        elif self.amount >= 0 and self.rule_type not in (
            RuleType.SPIN_OFF,
            RuleType.DIVIDEND,
            RuleType.INTEREST,
            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
        ):
            assert self.gain == self.amount + self.fees - self.allowable_cost, (
                f"Mismatch: {self.gain} != "
                f"{self.amount} + {self.fees} - {self.allowable_cost} (for {self})"
            )

    def __repr__(self) -> str:
        """Return print representation."""
        return f"<CalculationEntry {self!s}>"

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"{self.rule_type.name.replace('_', ' ')}, "
            f"quantity: {self.quantity}, "
            f"amount: {self.amount}, "
            f"allowable cost: {self.allowable_cost}, "
            f"fees: {self.fees}, "
            f"gain: {self.gain}, "
            f"new pool cost: {self.new_pool_cost}"
        )


CalculationLog = dict[datetime.date, dict[str, list[CalculationEntry]]]


@dataclass
class Position:
    """A single position in the portfolio."""

    quantity: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)

    def __add__(self, other: Position) -> Position:
        """Add two positions."""
        return Position(
            self.quantity + other.quantity,
            self.amount + other.amount,
        )

    def __sub__(self, other: Position) -> Position:
        """Subtract two positions."""
        return Position(
            self.quantity - other.quantity,
            self.amount - other.amount,
        )

    def __str__(self) -> str:
        """Return string representation."""
        return str(round_decimal(self.quantity, 2))


class PortfolioEntry:
    """A single symbol entry for the portfolio in the final report."""

    def __init__(
        self,
        symbol: str,
        quantity: Decimal,
        amount: Decimal,
        unrealized_gains: Decimal | None,
    ):
        """Create portfolio entry."""
        self.symbol = symbol
        self.quantity = quantity
        self.amount = amount
        self.unrealized_gains = unrealized_gains

    def unrealized_gains_str(self) -> str:
        """Format the unrealized gains to show in the report."""
        if self.unrealized_gains is None:
            str_val = "unknown"
        else:
            str_val = f"£{round_decimal(self.unrealized_gains, 2)}"

        return f" (unrealized gains: {str_val})"

    def __repr__(self) -> str:
        """Return print representation."""
        return f"<PortfolioEntry {self!s}>"

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"  {self.symbol}: {round_decimal(self.quantity, 2)}, "
            f"£{round_decimal(self.amount, 2)}"
        )


@dataclass
class CapitalGainsReport:
    """Store calculated report."""

    tax_year: int
    portfolio: list[PortfolioEntry]
    disposal_count: int
    disposal_proceeds: Decimal
    allowable_costs: Decimal
    capital_gain: Decimal
    capital_loss: Decimal
    capital_gain_allowance: Decimal | None
    dividend_allowance: Decimal | None
    calculation_log: CalculationLog
    calculation_log_yields: CalculationLog
    total_uk_interest: Decimal
    total_foreign_interest: Decimal
    show_unrealized_gains: bool

    def _filter_calculation_log(
        self, calculation_log: CalculationLog, rule_type: RuleType
    ) -> Generator[CalculationEntry]:
        for data in calculation_log.values():
            for entry_list in data.values():
                for entry in entry_list:
                    if entry.rule_type == rule_type:
                        yield entry

    def total_unrealized_gains(self) -> Decimal:
        """Total unrealized gains across portfolio."""
        return sum(
            (
                h.unrealized_gains
                for h in self.portfolio
                if h.unrealized_gains is not None
            ),
            Decimal(0),
        )

    def total_gain(self) -> Decimal:
        """Total capital gain."""
        return self.capital_gain + self.capital_loss

    def taxable_gain(self) -> Decimal:
        """Taxable gain with current allowance."""
        assert self.capital_gain_allowance is not None
        return max(Decimal(0), self.total_gain() - self.capital_gain_allowance)

    def total_eri_amount(self, is_interest: bool) -> Decimal:
        """Total dividends amount just from ERI."""
        total = Decimal(0)
        for item in self._filter_calculation_log(
            self.calculation_log_yields, RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION
        ):
            assert item.eris
            assert len(item.eris) == 1
            if item.eris[0].is_interest == is_interest:
                total += item.amount
        return total

    def total_dividends_amount(self) -> Decimal:
        """Total dividends amount."""
        total = Decimal(0)
        for item in self._filter_calculation_log(
            self.calculation_log_yields, RuleType.DIVIDEND
        ):
            assert item.dividend is not None
            if not item.dividend.is_interest:
                total += item.amount

        total += self.total_eri_amount(is_interest=False)

        return total

    def total_dividend_taxes_in_tax_treaties_amount(self) -> Decimal:
        """Total taxes to be reclaimed due to tax treaties."""
        total = Decimal(0)
        for item in self._filter_calculation_log(
            self.calculation_log_yields, RuleType.DIVIDEND
        ):
            assert item.dividend is not None
            if not item.dividend.is_interest:
                total += item.dividend.tax_treaty_amount
        return total

    def total_dividend_taxable_gain(self) -> Decimal:
        """Total taxable gain after all allowances."""
        return max(
            Decimal(0),
            self.total_dividends_amount()
            - (self.dividend_allowance or Decimal(0))
            - self.total_dividend_taxes_in_tax_treaties_amount(),
        )

    def __repr__(self) -> str:
        """Return string representation."""
        return f"<CalculationEntry: {self!s}>"

    def __str__(self) -> str:
        """Return string representation."""
        out = f"Portfolio at the end of {self.tax_year}/{self.tax_year + 1} tax year:\n"
        for entry in self.portfolio:
            if entry.quantity > 0:
                unrealized_gains_str = (
                    entry.unrealized_gains_str() if self.show_unrealized_gains else ""
                )
                out += f"{entry!s}{unrealized_gains_str}\n"
        eris = list(
            self._filter_calculation_log(
                self.calculation_log_yields,
                RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
            )
        )
        out += f"For tax year {self.tax_year}/{self.tax_year + 1}:\n"
        if eris:
            out += "Excess Reported Income:\n"
            for item in self._filter_calculation_log(
                self.calculation_log_yields,
                RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
            ):
                assert item.eris
                assert len(item.eris) == 1
                dist_type = "interest" if item.eris[0].is_interest else "dividend"
                out += f"  {item.eris[0].symbol}: £{round_decimal(item.amount, 2)} "
                out += f"(included as {dist_type})\n"

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
        if self.show_unrealized_gains:
            total_unrealized_gains = round_decimal(self.total_unrealized_gains(), 2)
            out += f"Total unrealized gains: £{total_unrealized_gains}\n"
            if any(h.unrealized_gains is None for h in self.portfolio):
                out += (
                    "WARNING: Some unrealized gains couldn't be calculated."
                    " Take a look at the symbols with unknown unrealized gains above"
                    " and factor in their prices.\n"
                )
        out += (
            "Total dividends proceeds: "
            f"£{round_decimal(self.total_dividends_amount(), 2)}\n"
        )
        if self.dividend_allowance is not None:
            out += (
                "Total amount of dividends tax yearly allowance: "
                f"£{round_decimal(self.dividend_allowance, 2)}\n"
            )
        if (
            self.dividend_allowance is not None
            or self.total_dividend_taxes_in_tax_treaties_amount() > 0
        ):
            out += (
                "Total taxable dividends proceeds: "
                f"£{round_decimal(self.total_dividend_taxable_gain(), 2)}\n"
            )
        out += f"Total UK interest proceeds: £{self.total_uk_interest}\n"
        out += f"Total foreign interest proceeds: £{self.total_foreign_interest}\n"

        return out
