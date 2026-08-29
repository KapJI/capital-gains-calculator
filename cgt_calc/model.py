"""Model classes."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
from decimal import Decimal
from enum import Enum
import re
import sys
from typing import TYPE_CHECKING, Final, NamedTuple, Self, override

from colorama import Style

from .exceptions import CalculationError
from .logging import bullet, style_text
from .util import (
    approx_equal,
    exact_str,
    luhn_check_digit,
    normalize_amount,
    round_decimal,
    strip_zeros,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


_ISIN_REGEX: Final = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_ISIN_CHARS: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CURRENCY_CODE_REGEX: Final = re.compile(r"^[A-Z]{3}$")


class Isin(str):
    """ISIN (ISO 6166) security identifier.

    Validated and normalised on construction, so every instance is a
    well-formed identifier with a correct check digit.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        """Normalise and validate the identifier."""
        normalised = value.strip().upper()
        if not _ISIN_REGEX.match(normalised):
            raise ValueError(f"Invalid ISIN: {value!r}")
        payload = "".join(str(_ISIN_CHARS.index(char)) for char in normalised[:11])
        if luhn_check_digit(payload) != int(normalised[11]):
            raise ValueError(f"Invalid ISIN checksum: {value!r}")
        return super().__new__(cls, normalised)

    @classmethod
    def parse(cls, value: str) -> Self | None:
        """Return the identifier, or None when the value is not an ISIN."""
        try:
            return cls(value)
        except ValueError:
            return None


class CurrencyCode(str):
    """ISO 4217 alpha-3 currency code.

    Only the shape is validated here: whether a code is a real, current
    currency is decided by the HMRC exchange rate table when it is used.
    Case is not folded because it can carry meaning: "GBp" is pence, not
    pounds, and quietly upper-casing it would misstate amounts a hundredfold.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        """Validate the code, tolerating surrounding whitespace."""
        normalised = value.strip()
        if not _CURRENCY_CODE_REGEX.match(normalised):
            raise ValueError(f"Invalid currency code: {value!r}")
        return super().__new__(cls, normalised)

    @classmethod
    def parse(cls, value: str) -> Self | None:
        """Return the code, or None when the value is not a currency code."""
        try:
            return cls(value)
        except ValueError:
            return None


@dataclass
class SpinOff:
    """Class representing spin-off event on a share."""

    # Source of the Spin-off, e.g. MMM for SOLV
    source: str
    # Destination ticker to which SpinOff happened, e.g. SOLV for MMM
    dest: str
    # When the spin-off happened
    date: datetime.date
    # Shares received under this row, and the closing prices on the day. A
    # holding split across brokers arrives as one row per broker; the split
    # of value is worked out across all of them together.
    quantity: Decimal = Decimal(0)
    source_price: Decimal | None = None
    dest_price: Decimal | None = None


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
    """HMRC transaction figures."""

    quantity: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    # This is a list to support Bed and Breakfast acquisitions that can cross multiple
    # ERI reports for the same fund. This can happen for example when a fund is
    # liquidated shortly after its usual reporting date, requiring a new final report.
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
    currency: CurrencyCode | None = None

    def __add__(self, amount: ForeignCurrencyAmount) -> ForeignCurrencyAmount:
        """Add two amounts."""
        if self.currency is None and self.amount:
            raise CalculationError(f"Currency missing for amount {self.amount}")
        if amount.currency is None and amount.amount:
            raise CalculationError(f"Currency missing for amount {amount.amount}")
        if (
            self.currency is not None
            and amount.currency is not None
            and self.currency != amount.currency
        ):
            raise CalculationError(
                "Cannot combine amounts in different currencies: "
                f"{self.currency} and {amount.currency}"
            )
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


class DividendTaxAttribution(NamedTuple):
    """Withheld tax sorted by whether a dividend was found to carry it."""

    matched: ForeignAmountLog
    """Keyed by the symbol and date of the dividend the tax was taken from."""

    unmatched: ForeignAmountLog
    """Keyed by the symbol and date of the withholding itself.

    No dividend row can carry this tax, but the broker took it all the same,
    so the summary reports it against the year it was taken in.
    """


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
    FULL_REDEMPTION = 18
    RENAME = 19
    INTEREST_TAX = 20
    TRANSFER_TO_SPOUSE = 21
    # A broker reported shares leaving as a gift without saying who received
    # them. The user classifies it with a TRANSFER_TO_SPOUSE or GIFT row.
    UNCLASSIFIED_GIFT = 22
    TRANSFER_FROM_SPOUSE = 23
    # Shares given to a connected person other than a spouse: a disposal at
    # market value, with a loss clogged under TCGA 1992 s18(3).
    GIFT = 24
    # The same, to someone who is not a connected person: a loss is ordinary.
    GIFT_UNCONNECTED = 25
    # A purchase the broker reversed. Parsers are expected to drop it along
    # with the purchase it reverses, so it never reaches the calculator; it
    # exists so that a row which slips through is refused rather than booked
    # as an acquisition.
    CANCEL_BUY = 26


class CalculationType(Enum):
    """Calculation type enumeration."""

    ACQUISITION = 1
    DISPOSAL = 2


@dataclass(frozen=True)
class TransactionSource:
    """Where a transaction was read from.

    ``index`` is the position the parser read this row at within one source
    file, and it is what says which of two rows on one day came first. It is
    not ``row``: a parser is free to emit rows in an order the file does not
    use, and Freetrade does exactly that because its export is newest first.
    ``row`` is the line the row came from, for pointing an error at it.

    ``account`` is an opaque token for the boundary the user declared by
    passing one file or one directory. It is calculation-local: it says that
    two transactions were configured as one account, never which account, and
    nothing about it may be read as a broker account identifier.
    """

    parser: str | None = None
    account: str | None = None
    file: Path | None = None
    row: int | None = None
    index: int | None = None
    # The instant the export stated, where it states one at all.
    timestamp: datetime.datetime | None = None
    # Whether this source documents that rows sharing a date are in the order
    # they happened. A hand-written RAW history says so; a broker export does
    # not, and several parsers reorder their rows for the calculation's sake,
    # so `index` says which row was read first and nothing more.
    rows_in_time_order: bool = False


@dataclass
class BrokerTransaction:
    """Broker transaction data."""

    date: datetime.date
    action: ActionType
    symbol: str | None
    description: str
    quantity: Decimal | None
    price: Decimal | None
    fees: Decimal
    amount: Decimal | None
    currency: CurrencyCode
    broker: str
    isin: Isin | None = None
    # Fees paid in currencies other than `currency`, keyed by their own
    # currency. Converted and folded into `fees` by the calculation engine.
    foreign_fees: dict[CurrencyCode, Decimal] = field(default_factory=dict)
    # The other count this row could be stating, where an export does not say
    # whether a share count predating a split was restated for it. Set only
    # when the two cannot be told apart, so that whichever the user confirms
    # can settle it.
    ambiguous_quantity: Decimal | None = None
    # Where this row was read from. Excluded from equality and from any
    # __hash__: parsers deduplicate overlapping exports by comparing
    # transactions, and a field that varies with the file would keep both
    # copies of every row two exports share. Out of the repr too, which is
    # printed whole in several errors and is about the transaction, not about
    # which file on this machine it was read from.
    source: TransactionSource | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Validate BrokerTransaction data."""
        # Callers that bypass the type checker still get validated values;
        # to mypy the annotations already rule this out.
        if not isinstance(self.currency, CurrencyCode):
            self.currency = CurrencyCode(self.currency)  # type: ignore[unreachable]
        if self.isin is not None and not isinstance(self.isin, Isin):
            self.isin = Isin(self.isin)  # type: ignore[unreachable]
        if any(not isinstance(key, CurrencyCode) for key in self.foreign_fees):
            # Keys that normalise to the same code are fees in one currency,
            # so they add up rather than overwrite each other.
            coerced: dict[CurrencyCode, Decimal] = {}
            for key, fee in self.foreign_fees.items():
                code = CurrencyCode(key)
                coerced[code] = coerced.get(code, Decimal(0)) + fee
            self.foreign_fees = coerced


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
    RENAME = 9
    INTEREST_TAX = 10
    TRANSFER_TO_SPOUSE = 11


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
        """Dividend amount reclaimable under the tax treaty (0 if none)."""
        if self.tax_treaty is None:
            return Decimal(0)
        return self.amount * self.tax_treaty.treaty_rate


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
        bed_and_breakfast_date_index: datetime.date | None = None,
        spin_off: SpinOff | None = None,
        dividend: Dividend | None = None,
        eris: list[ExcessReportedIncome] | None = None,
        renamed_to: str | None = None,
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
        self.renamed_to = renamed_to
        if self.rule_type == RuleType.EXCESS_REPORTED_INCOME:
            assert self.allowable_cost > 0, str(self)
            assert approx_equal(
                self.allowable_cost, self.amount + self.new_pool_cost
            ), (
                f"Mismatch: {self.allowable_cost} != "
                f"{self.amount} + {self.new_pool_cost} (for {self})"
            )
        elif self.amount >= 0 and self.rule_type not in {
            RuleType.SPIN_OFF,
            RuleType.DIVIDEND,
            RuleType.INTEREST,
            RuleType.INTEREST_TAX,
            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
            RuleType.RENAME,
        }:
            assert self.gain == self.amount + self.fees - self.allowable_cost, (
                f"Mismatch: {self.gain} != "
                f"{self.amount} + {self.fees} - {self.allowable_cost} (for {self})"
            )

    @override
    def __repr__(self) -> str:
        """Return print representation."""
        return f"<CalculationEntry {self!s}>"

    @override
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
            normalize_amount(self.amount + other.amount),
        )

    def __sub__(self, other: Position) -> Position:
        """Subtract two positions."""
        return Position(
            self.quantity - other.quantity,
            normalize_amount(self.amount - other.amount),
        )

    @override
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
            str_val = f"£{round_decimal(self.unrealized_gains, 2):,}"

        return f" (unrealized gains: {str_val})"

    @override
    def __repr__(self) -> str:
        """Return print representation."""
        return f"<PortfolioEntry {self!s}>"

    @override
    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"{self.symbol}: {round_decimal(self.quantity, 2):,}, "
            f"£{round_decimal(self.amount, 2):,}"
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
    total_interest_tax: Decimal
    show_unrealized_gains: bool
    # Custom reporting period within the tax year, if one was requested.
    period_start: datetime.date | None = None
    period_end: datetime.date | None = None
    # Losses on gifts, kept out of capital_loss: a loss on a disposal to a
    # connected person is a clogged loss (TCGA 1992 s18(3)). Negative or zero.
    gift_loss: Decimal = Decimal(0)

    def period_label(self) -> str | None:
        """Label for a custom reporting period, None for a full tax year."""
        if self.period_start is None or self.period_end is None:
            return None
        return f"period {self.period_start} to {self.period_end}"

    @property
    def title_period(self) -> str:
        """Reporting period rendered in the PDF report title."""
        if self.period_start is not None and self.period_end is not None:
            return f"{self.period_start} to {self.period_end}"
        return f"{self.tax_year}-{(self.tax_year + 1) % 100:02d}"

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
        if self.capital_gain_allowance is None:
            raise CalculationError(
                f"Taxable gain cannot be calculated because the capital gains "
                f"allowance for {self.tax_year}/{self.tax_year + 1} is unavailable"
            )
        return max(Decimal(0), self.total_gain() - self.capital_gain_allowance)

    def total_eri_amount(self, *, is_interest: bool) -> Decimal:
        """Return the total ERI distribution amount.

        Covers interest funds if is_interest is True, otherwise dividend funds.
        """
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

    @override
    def __repr__(self) -> str:
        """Return print representation."""
        return f"<CapitalGainsReport: {self!s}>"

    @override
    def __str__(self) -> str:
        """Return string representation."""
        bul = bullet(sys.stdout)
        portfolio_label = (
            self.period_label() or f"{self.tax_year}/{self.tax_year + 1} tax year"
        )
        out = (
            style_text(
                f"Portfolio at the end of {portfolio_label}",
                colour=Style.BRIGHT,
                emoji="📈",
            )
            + "\n"
        )
        held = [entry for entry in self.portfolio if entry.quantity > 0]
        if not held:
            out += f"{bul}(none)\n"
        for entry in sorted(held, key=lambda entry: entry.symbol):
            unrealized_gains_str = (
                entry.unrealized_gains_str() if self.show_unrealized_gains else ""
            )
            out += f"{bul}{entry!s}{unrealized_gains_str}\n"
        eris = list(
            self._filter_calculation_log(
                self.calculation_log_yields,
                RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
            )
        )
        out += "\n"
        out += (
            style_text(
                "Tax summary for "
                f"{self.period_label() or f'{self.tax_year}/{self.tax_year + 1}'}",
                colour=Style.BRIGHT,
                emoji="🧮",
            )
            + "\n"
        )

        capital: list[tuple[str, str]] = [
            ("Disposals", str(self.disposal_count)),
            ("Disposal proceeds", f"£{self.disposal_proceeds:,}"),
            ("Allowable costs", f"£{self.allowable_costs:,}"),
            ("Gain", f"£{self.capital_gain:,}"),
            ("Loss", f"£{-self.capital_loss:,}"),
            *([("Losses on gifts", f"£{-self.gift_loss:,}")] if self.gift_loss else []),
            ("Total gain", f"£{self.total_gain():,}"),
        ]
        capital_notes: list[str] = []
        if self.capital_gain_allowance is not None:
            capital.append(
                ("Taxable gain", f"£{round_decimal(self.taxable_gain(), 2):,}")
            )
        else:
            capital_notes.append("WARNING: Missing allowance for this tax year")
        if self.show_unrealized_gains:
            capital.append(
                (
                    "Unrealized gains",
                    f"£{round_decimal(self.total_unrealized_gains(), 2):,}",
                )
            )
            if any(h.unrealized_gains is None for h in self.portfolio):
                capital_notes.append(
                    "WARNING: Some unrealized gains couldn't be calculated."
                    " Take a look at the symbols with unknown unrealized gains"
                    " above and factor in their prices."
                )

        dividends: list[tuple[str, str]] = [
            ("Proceeds", f"£{round_decimal(self.total_dividends_amount(), 2):,}"),
        ]
        if self.dividend_allowance is not None:
            dividends.append(
                (
                    "Tax-free allowance",
                    f"£{round_decimal(self.dividend_allowance, 2):,}",
                )
            )
        if (
            self.dividend_allowance is not None
            or self.total_dividend_taxes_in_tax_treaties_amount() > 0
        ):
            dividends.append(
                (
                    "Taxable proceeds",
                    f"£{round_decimal(self.total_dividend_taxable_gain(), 2):,}",
                )
            )

        interest: list[tuple[str, str]] = [
            ("UK proceeds", f"£{self.total_uk_interest:,}"),
            ("Foreign proceeds", f"£{self.total_foreign_interest:,}"),
            ("Tax paid", f"£{self.total_interest_tax:,}"),
        ]

        groups: list[tuple[str, list[tuple[str, str]], list[str]]] = [
            ("Capital gains", capital, capital_notes),
            ("Dividends", dividends, []),
            ("Interest", interest, []),
        ]

        # Right-align values on one shared column across the whole summary so
        # the decimal points line up.
        label_width = max(len(label) for _, rows, _ in groups for label, _ in rows) + 1
        value_width = max(len(value) for _, rows, _ in groups for _, value in rows)
        for title, rows, notes in groups:
            out += f"\n{style_text(title, colour=Style.BRIGHT)}\n"
            for label, value in rows:
                line = f"  {label + ':':<{label_width}} {value:>{value_width}}"
                if label in {"Total gain", "Taxable gain"}:
                    # The headline figures of the whole report.
                    line = style_text(line, colour=Style.BRIGHT)
                out += f"{line}\n"
            for note in notes:
                out += f"{note}\n"

        if eris:
            out += (
                "\n" + style_text("Excess Reported Income", colour=Style.BRIGHT) + "\n"
            )
            for item in eris:
                assert item.eris
                assert len(item.eris) == 1
                dist_type = "interest" if item.eris[0].is_interest else "dividend"
                out += (
                    f"{bul}{item.eris[0].symbol}: £{round_decimal(item.amount, 2):,} "
                )
                out += f"(included as {dist_type})\n"

        transfer_prefix = "transfer-to-spouse$"
        transfers = sorted(
            (
                (date_index, key, entry_list)
                for date_index, symbol_dict in self.calculation_log.items()
                for key, entry_list in symbol_dict.items()
                if key.startswith(transfer_prefix)
            ),
            key=lambda transfer: (transfer[0], transfer[1]),
        )
        if transfers:
            out += (
                "\n" + style_text("Transferred to spouse", colour=Style.BRIGHT) + "\n"
            )
            out += (
                "  No gain/no loss; the base cost below passes to the recipient"
                " (TCGA 1992 s58).\n"
                "  Give them the RAW row shown under each transfer: it records the"
                " shares arriving at that cost in their own report.\n"
            )
            for date_index, key, entry_list in transfers:
                symbol = key[len(transfer_prefix) :]
                quantity = sum((e.quantity for e in entry_list), Decimal(0))
                base_cost = sum((e.allowable_cost for e in entry_list), Decimal(0))
                out += (
                    f"{bul}{date_index}: {symbol} "
                    f"{strip_zeros(quantity)} units, base cost "
                    f"£{round_decimal(base_cost, 2):,}\n"
                    f"    {date_index},TRANSFER_FROM_SPOUSE,{symbol},"
                    f"{exact_str(quantity)},{exact_str(base_cost / quantity)},"
                    "0.00,GBP\n"
                )

        gift_prefixes = ("gift$", "gift-unconnected$")
        gifts = sorted(
            (
                (date_index, key, entry_list)
                for date_index, symbol_dict in self.calculation_log.items()
                for key, entry_list in symbol_dict.items()
                if key.startswith(gift_prefixes)
            ),
            key=lambda gift: (gift[0], gift[1]),
        )
        if gifts:
            out += (
                "\n" + style_text("Gifts at market value", colour=Style.BRIGHT) + "\n"
            )
            out += (
                "  Disposals at market value (TCGA 1992 s17), before any relief."
                " A loss on a gift to a connected\n"
                "  person (GIFT) is clogged: kept out of Loss and Total gain, usable"
                " only against gains on disposals\n"
                "  to the same person while still connected (s18(3)). Keep a"
                " separate record of it. A loss on a gift\n"
                "  to anyone else (GIFT_UNCONNECTED) counts in Loss.\n"
            )
            for date_index, key, entry_list in gifts:
                clogged = key.startswith("gift$")
                symbol = key.split("$", 1)[1]
                quantity = sum((e.quantity for e in entry_list), Decimal(0))
                market_value = sum((e.amount + e.fees for e in entry_list), Decimal(0))
                gain = sum((e.gain for e in entry_list), Decimal(0))
                if gain < 0:
                    outcome = f"loss £{round_decimal(-gain, 2):,}"
                    if clogged:
                        outcome += " (clogged)"
                else:
                    outcome = f"gain £{round_decimal(gain, 2):,}"
                out += (
                    f"{bul}{date_index}: {symbol} {strip_zeros(quantity)} units, "
                    f"market value £{round_decimal(market_value, 2):,}, {outcome}\n"
                )

        return out
