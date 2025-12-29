"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
import re
from typing import TYPE_CHECKING, Final, TextIO, cast

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
    from pathlib import Path


class VanguardColumn(StrEnum):
    """Columns exported in the Vanguard transaction CSV."""

    DATE = "Date"
    DETAILS = "Details"
    AMOUNT = "Amount"
    BALANCE = "Balance"


COLUMNS: Final[list[str]] = [column.value for column in VanguardColumn]

BOUGHT_RE = re.compile(r"^Bought (\d*[,]?\d*) .*\((.*)\)$")
SOLD_RE = re.compile(r"^Sold (\d*[,]?\d*) .*\((.*)\)$")
DIV_RE = re.compile(r"^DIV: ([^\.]+)\.[^ ]+ @ ([A-Z]+) (\d*[,\.]?\d*)")
TRANSFER_RE = re.compile(
    r".*(Regular Deposit|Deposit via|Deposit for|Payment by|Account Fee).*"
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


class VanguardTransaction(BrokerTransaction):
    """Represents a single Vanguard transaction."""

    is_reversal: bool

    def __init__(
        self,
        header: list[str],
        row_raw: list[str],
        file: Path,
    ):
        """Create transaction from CSV row."""
        currency = "GBP"
        broker = "Vanguard"

        if len(row_raw) != len(COLUMNS):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), file)

        row = dict(zip(header, row_raw, strict=False))

        date_str = row[VanguardColumn.DATE]
        date = datetime.datetime.strptime(date_str, "%d/%m/%Y").date()

        details = row[VanguardColumn.DETAILS]
        self.is_reversal = False
        if details.startswith(REVERSAL_STR):
            details = details[len(REVERSAL_STR) :]
            self.is_reversal = True

        action = action_from_str(details, file)

        fees = Decimal(0)
        amount = _parse_decimal(row[VanguardColumn.AMOUNT], VanguardColumn.AMOUNT.value)

        quantity = None
        price = None
        symbol = None
        if action == ActionType.BUY:
            match = BOUGHT_RE.match(details)
            assert match
            quantity = _parse_decimal(match.group(1), "Details quantity")
            symbol = match.group(2)
            price = abs(amount) / quantity
        elif action == ActionType.SELL:
            match = SOLD_RE.match(details)
            assert match
            quantity = _parse_decimal(match.group(1), "Details quantity")
            symbol = match.group(2)
            price = amount / quantity
        elif action == ActionType.DIVIDEND:
            match = DIV_RE.match(details)
            assert match
            symbol = match.group(1)
            currency = match.group(2)
            price = _parse_decimal(match.group(3), "Details price")
            quantity = Decimal(round(amount / price))

        super().__init__(
            date,
            action,
            symbol,
            "",
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
        )


def by_date_and_action(
    transaction: VanguardTransaction,
) -> tuple[datetime.date, bool, bool]:
    """Sort by date and action type."""
    # Deprioritize BUY and reversal transaction to prevent balance errors
    return (
        transaction.date,
        transaction.action == ActionType.BUY,
        transaction.is_reversal,
    )


class VanguardParser(BaseSingleFileParser):
    """Parser for Vanguard transaction files."""

    arg_name = "vanguard"
    pretty_name = "Vanguard"
    format_name = "CSV"

    @classmethod
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read Vanguard transactions from file."""
        lines = list(csv.reader(file))

        if not lines:
            raise ParsingError(file_path, "Vanguard CSV file is empty")
        header = lines[0]
        cls._validate_header(header, file_path)

        transactions: list[VanguardTransaction] = []
        for index, row in enumerate(lines[1:], start=2):
            try:
                transactions.append(VanguardTransaction(header, row, file_path))
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
        transactions.sort(key=by_date_and_action)
        return cast("list[BrokerTransaction]", transactions)

    @classmethod
    def _validate_header(cls, header: list[str], file: Path) -> None:
        """Check if header is valid."""

        if len(header) != len(COLUMNS):
            raise UnexpectedColumnCountError(header, len(COLUMNS), file)

        for index, (exp, act) in enumerate(zip(COLUMNS, header, strict=True), start=1):
            if exp != act:
                raise ParsingError(
                    file,
                    f"Expected column {index} to be '{exp}' but found '{header[index - 1]}'",
                )
