"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import io
import logging
import re
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, cast

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
    from pathlib import Path


class CashColumn(StrEnum):
    """Columns in the Cash Transactions table."""

    DATE = "Date"
    DETAILS = "Details"
    AMOUNT = "Amount"
    BALANCE = "Balance"


class InvestmentColumn(StrEnum):
    """Columns in the Investment Transactions table."""

    DATE = "Date"
    INVESTMENT_NAME = "InvestmentName"
    TRANSACTION_DETAILS = "TransactionDetails"
    QUANTITY = "Quantity"
    PRICE = "Price"
    COST = "Cost"


CASH_COLUMNS: Final[set[str]] = {c.value for c in CashColumn}
INVESTMENT_COLUMNS: Final[set[str]] = {c.value for c in InvestmentColumn}
# Column names (excluding Date) that signal a summary/footer row when they
# appear as the first cell of a row (e.g. "Balance,23.59" or "Cost,12545").
_SUMMARY_LABELS: Final[set[str]] = (CASH_COLUMNS | INVESTMENT_COLUMNS) - {"Date"}


class TableType(StrEnum):
    """Type of Vanguard CSV table."""

    CASH = "cash"
    INVESTMENT = "investment"


# Backward-compatible alias used by tests
COLUMNS: Final[list[str]] = [c.value for c in CashColumn]

BOUGHT_RE = re.compile(r"^Bought ([\d,]*\.?\d+) (.+?)(?:\s*\(([^()]+)\))?$")
SOLD_RE = re.compile(r"^Sold ([\d,]*\.?\d+) (.+?)(?:\s*\(([^()]+)\))?$")
DIV_RE = re.compile(r"^DIV: ([^\.]+)\.[^ ]+ @ ([A-Z]+) (\d*[,\.]?\d*)")
TRANSFER_RE = re.compile(
    r".*(Regular Deposit|Cash transfer|Deposit via|Deposit for|Payment by|"
    r"Account [Ff]ee|ETF dealing fee).*"
)
NAMECHANGE_RE = re.compile(
    r"^NameChange:\s*([A-Z0-9]+)(?:\.\S+)?"
    r"(?:\s+replaced with\s+([A-Z0-9]+)(?:\.\S+)?)?\s*$"
)

INTEREST_STR = "Cash Account Interest"
REVERSAL_STR = "Reversal of "
LOGGER = logging.getLogger(__name__)


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert label to ActionType."""

    if TRANSFER_RE.match(label):
        return ActionType.TRANSFER

    if BOUGHT_RE.match(label):
        return ActionType.BUY

    if SOLD_RE.match(label):
        return ActionType.SELL

    if DIV_RE.match(label):
        return ActionType.DIVIDEND

    if label == INTEREST_STR:
        return ActionType.INTEREST

    raise ParsingError(file, f"Unknown action: {label}")


def _parse_decimal(value: str, context: str) -> Decimal:
    """Parse decimal value, raising ValueError with contextual message on failure."""

    normalized = value.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(f"Invalid decimal in {context}: {value!r}") from err


def _parse_details(
    details: str, action: ActionType, amount: Decimal, file: Path
) -> tuple[str | None, Decimal | None, Decimal | None, str]:
    """Extract symbol, quantity, price, currency from a Details string."""
    currency = "GBP"
    quantity = None
    price = None
    symbol = None
    if action == ActionType.BUY:
        match = BOUGHT_RE.match(details)
        assert match
        quantity = _parse_decimal(match.group(1), "Details quantity")
        symbol = match.group(3) or match.group(2).strip()
        price = abs(amount) / quantity
    elif action == ActionType.SELL:
        match = SOLD_RE.match(details)
        assert match
        quantity = _parse_decimal(match.group(1), "Details quantity")
        symbol = match.group(3) or match.group(2).strip()
        price = amount / quantity
    elif action == ActionType.DIVIDEND:
        match = DIV_RE.match(details)
        assert match
        symbol = match.group(1)
        currency = match.group(2)
        price = _parse_decimal(match.group(3), "Details price")
        quantity = Decimal(round(amount / price))
    return symbol, quantity, price, currency


def _strip_reversal(details: str) -> tuple[str, bool]:
    """Strip reversal prefix from details, returning cleaned string and flag."""
    if details.startswith(REVERSAL_STR):
        return details[len(REVERSAL_STR) :], True
    return details, False


def _symbol_from_details(details: str) -> str | None:
    """Extract symbol from a buy/sell/dividend details string."""
    match = BOUGHT_RE.match(details) or SOLD_RE.match(details)
    if match:
        return match.group(3) or match.group(2).strip()
    match = DIV_RE.match(details)
    if match:
        return match.group(1)
    return None


class VanguardTransaction(BrokerTransaction):
    """Represents a single Vanguard transaction."""

    is_reversal: bool
    details_text: str
    source_file: Path

    def __init__(
        self,
        header: list[str],
        row_raw: list[str],
        file: Path,
    ):
        """Create transaction from a cash table CSV row."""
        if len(row_raw) != len(header):
            raise UnexpectedColumnCountError(row_raw, len(header), file)

        row = dict(zip(header, row_raw, strict=False))

        date = datetime.datetime.strptime(row[CashColumn.DATE], "%d/%m/%Y").date()
        self.details_text, self.is_reversal = _strip_reversal(row[CashColumn.DETAILS])
        self.source_file = file
        self.action = action_from_str(self.details_text, file)
        self.amount = _parse_decimal(row[CashColumn.AMOUNT], CashColumn.AMOUNT.value)
        self.symbol, self.quantity, self.price, self.currency = _parse_details(
            self.details_text, self.action, self.amount, self.source_file
        )

        super().__init__(
            date=date,
            action=self.action,
            symbol=self.symbol,
            description="",
            quantity=self.quantity,
            price=self.price,
            fees=Decimal(0),
            amount=self.amount,
            currency="GBP",
            broker="Vanguard",
            isin=None,
        )

    def enrich_details(self, investment_row: dict[str, str] | None = None) -> None:
        """Fill investment transaction details missing from the cash transaction table."""
        if investment_row is not None and self.quantity is None:
            assert self.amount is not None
            self.symbol = _symbol_from_details(self.details_text)
            self.quantity = abs(
                _parse_decimal(
                    investment_row[InvestmentColumn.QUANTITY],
                    InvestmentColumn.QUANTITY.value,
                )
            )
            self.price = self.amount / self.quantity

    @classmethod
    def from_fields(
        cls,
        date: datetime.date,
        action: ActionType,
        symbol: str | None,
        quantity: Decimal | None,
        price: Decimal | None,
        amount: Decimal | None,
        currency: str,
        is_reversal: bool,
        description: str = "",
    ) -> VanguardTransaction:
        """Create a VanguardTransaction from pre-parsed fields."""
        txn = object.__new__(cls)
        txn.is_reversal = is_reversal
        txn.details_text = ""
        txn.source_file = None  # type: ignore[assignment]
        BrokerTransaction.__init__(
            txn,
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            Decimal(0),
            amount,
            currency,
            "Vanguard",
        )
        return txn


def _make_transaction_from_investment(
    row: dict[str, str],
    file: Path,
) -> VanguardTransaction | None:
    """Create a VanguardTransaction from an Investment row.

    Returns a RENAME transaction for NameChange rows, None for the zero-out
    half of a NameChange pair, or a normal BUY/SELL/DIVIDEND transaction.
    """
    details, is_reversal = _strip_reversal(row[InvestmentColumn.TRANSACTION_DETAILS])
    match = NAMECHANGE_RE.match(details)
    if match:
        old_ticker, new_ticker = match.group(1), match.group(2)
        # NameChange rows come in a zero-out/add-new pair; only the "replaced
        # with" half carries both tickers. Skip the other half.
        if not new_ticker:
            return None
        date = datetime.datetime.strptime(row[InvestmentColumn.DATE], "%d/%m/%Y").date()
        return VanguardTransaction.from_fields(
            date=date,
            action=ActionType.RENAME,
            symbol=new_ticker,
            quantity=Decimal(0),
            price=None,
            amount=Decimal(0),
            currency="GBP",
            is_reversal=False,
            description=f"{RENAME_DESCRIPTION_PREFIX}{old_ticker}",
        )
    date = datetime.datetime.strptime(row[InvestmentColumn.DATE], "%d/%m/%Y").date()
    action = action_from_str(details, file)
    symbol = _symbol_from_details(details)

    return VanguardTransaction.from_fields(
        date=date,
        action=action,
        symbol=symbol,
        quantity=abs(
            _parse_decimal(
                row[InvestmentColumn.QUANTITY], InvestmentColumn.QUANTITY.value
            )
        ),
        price=_parse_decimal(row[InvestmentColumn.PRICE], InvestmentColumn.PRICE.value),
        amount=_parse_decimal(row[InvestmentColumn.COST], InvestmentColumn.COST.value),
        currency="GBP",
        is_reversal=is_reversal,
    )


def by_date_and_action(
    transaction: VanguardTransaction,
) -> tuple[datetime.date, bool, bool, bool]:
    """Sort by date and action type."""
    # Deprioritize BUY and reversal transaction to prevent balance errors
    # Deprioritize debits (e.g. fee charged) so credits (e.g. fee cleared)
    # are processed first, preventing temporary negative balances.
    is_debit = transaction.amount is not None and transaction.amount < 0
    return (
        transaction.date,
        transaction.action == ActionType.BUY,
        is_debit,
        transaction.is_reversal,
    )


def _detect_delimiter(text: str) -> str:
    """Detect whether the file uses comma or tab as delimiter."""
    first_line = text.split("\n", 1)[0]
    if "\t" in first_line:
        return "\t"
    return ","


def _split_tables(
    lines: list[list[str]],
) -> tuple[list[list[str]] | None, list[list[str]] | None]:
    """Split parsed CSV lines into cash and investment table sections by header detection."""
    cash_table: list[list[str]] | None = None
    investment_table: list[list[str]] | None = None

    current: list[list[str]] = []
    current_type: TableType | None = None

    def _flush() -> None:
        nonlocal cash_table, investment_table
        if current_type == TableType.CASH:
            cash_table = list(current)
        elif current_type == TableType.INVESTMENT:
            investment_table = list(current)

    for row in lines:
        stripped = {c.strip() for c in row}
        if stripped >= CASH_COLUMNS:
            _flush()
            current = [row]
            current_type = TableType.CASH
        elif stripped >= INVESTMENT_COLUMNS:
            _flush()
            current = [row]
            current_type = TableType.INVESTMENT
        elif current_type and row and any(c.strip() for c in row):
            first = row[0].strip()
            if first in _SUMMARY_LABELS:
                # Summary/footer row (e.g. "Balance,23.59") — end current table.
                _flush()
                current = []
                current_type = None
            else:
                current.append(row)

    _flush()
    return cash_table, investment_table


def _find_investment_match(
    txn: VanguardTransaction,
    investment_lookup: dict[str, list[dict[str, str]]],
) -> dict[str, str] | None:
    """Find the best matching investment row for a cash transaction.

    When multiple investment rows share the same TransactionDetails key:
    - BUY: earliest investment date on or after the cash date (cash transaction happen first, then investment buy)
    - SELL: latest investment date on or before the cash date (investment sell happen first, then cash in)
    """
    candidates = investment_lookup.get(txn.details_text)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _investment_date(r: dict[str, str]) -> datetime.date:
        return datetime.datetime.strptime(r[InvestmentColumn.DATE], "%d/%m/%Y").date()

    if txn.action == ActionType.BUY:
        after = [r for r in candidates if _investment_date(r) >= txn.date]
        return min(after, key=_investment_date) if after else None
    # SELL or other: latest date on or before the cash date
    before = [r for r in candidates if _investment_date(r) <= txn.date]
    return max(before, key=_investment_date) if before else None


class VanguardParser(BaseSingleFileParser):
    """Parser for Vanguard transaction files."""

    arg_name = "vanguard"
    pretty_name = "Vanguard"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--vanguard"]

    @classmethod
    def _validate_cash_header(cls, header: list[str], file: Path) -> None:
        """Check if header matches the cash transactions columns."""
        expected = [c.value for c in CashColumn]
        if len(header) != len(expected):
            raise UnexpectedColumnCountError(header, len(expected), file)
        for index, (exp, act) in enumerate(zip(expected, header, strict=True), start=1):
            if exp != act:
                raise ParsingError(
                    file,
                    f"Expected column {index} to be '{exp}' but found '{act}'",
                )

    @classmethod
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read Vanguard transactions from exported transaction report file."""
        raw_text = file.read()
        delimiter = _detect_delimiter(raw_text)
        lines = list(csv.reader(io.StringIO(raw_text), delimiter=delimiter))

        if not lines:
            raise ParsingError(file_path, "Vanguard CSV file is empty")

        cash_lines, investment_lines = _split_tables(lines)

        if cash_lines is None and investment_lines is None:
            # Fallback: treat entire file as a plain cash table (old format)
            cls._validate_cash_header(lines[0], file_path)
            cash_lines = lines

        # If "Investment Transaction" table exists, build investment lookup keyed by TransactionDetails
        # This will be merged with "Cash Transaction" table.
        # RENAME transactions are emitted here so cash-only and investment-only
        # modes share one emission path.
        investment_lookup: dict[str, list[dict[str, str]]] = {}
        transactions: list[VanguardTransaction] = []
        if investment_lines is not None:
            investment_header = investment_lines[0]
            for row in investment_lines[1:]:
                if len(row) != len(investment_header):
                    continue
                investment_row = dict(zip(investment_header, row, strict=False))
                txn = _make_transaction_from_investment(investment_row, file_path)
                if txn is not None and txn.action is ActionType.RENAME:
                    transactions.append(txn)
                    continue
                key = investment_row.get(InvestmentColumn.TRANSACTION_DETAILS, "")
                if key:
                    investment_lookup.setdefault(key, []).append(investment_row)

        if cash_lines is not None:
            cash_header = cash_lines[0]
            for index, row in enumerate(cash_lines[1:], start=2):
                if len(row) != len(cash_header):
                    continue
                try:
                    transactions.append(
                        VanguardTransaction(cash_header, row, file_path)
                    )
                except ParsingError as err:
                    err.add_row_context(index)
                    raise
                except ValueError as err:
                    raise ParsingError(file_path, str(err), row_index=index) from err

            # Resolve missing quantity/price with values from the investment table, see
            # https://github.com/KapJI/capital-gains-calculator/issues/758
            for txn in transactions:
                matched_inv = _find_investment_match(txn, investment_lookup)
                txn.enrich_details(matched_inv)

        elif investment_lines is not None:
            # Only investment table present
            investment_header = investment_lines[0]
            for index, row in enumerate(investment_lines[1:], start=2):
                if len(row) != len(investment_header):
                    continue
                investment_row = dict(zip(investment_header, row, strict=False))
                try:
                    inv_txn = _make_transaction_from_investment(
                        investment_row, file_path
                    )
                except ParsingError as err:
                    err.add_row_context(index)
                    raise
                except ValueError as err:
                    raise ParsingError(file_path, str(err), row_index=index) from err
                # RENAME transactions are already emitted by the lookup pass above.
                if inv_txn is not None and inv_txn.action is not ActionType.RENAME:
                    transactions.append(inv_txn)

        transactions.sort(key=by_date_and_action)
        return cast("list[BrokerTransaction]", transactions)
