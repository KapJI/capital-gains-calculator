"""Revolut transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import chain
import logging
import re
from typing import TYPE_CHECKING, ClassVar, Final, Literal, TextIO, overload, override

from cgt_calc.const import TICKER_RENAMES, UK_TIMEZONE
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode

from .base_parsers import StandardCSVParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class RevolutColumn(StrEnum):
    """Column names for the Revolut format."""

    DATE = "Date"
    TICKER = "Ticker"
    ACTION = "Type"
    QUANTITY = "Quantity"
    PRICE_PER_SHARE = "Price per share"
    TOTAL_AMOUNT = "Total Amount"
    CURRENCY = "Currency"
    FX_RATE = "FX Rate"


COLUMNS: Final[list[str]] = [column.value for column in RevolutColumn]
HEADER_LINE: Final = ",".join(COLUMNS) + "\n"
LOGGER = logging.getLogger(__name__)


def _parse_date(value: str) -> datetime.date:
    """Convert a Revolut timestamp to the UK date it fell on.

    The export states its times in UTC, so a value without a zone is read
    as UTC too rather than as the machine's local time. The date that
    drives the tax year and the matching rules is the UK one.
    """

    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"Invalid timestamp: {value!r}") from err
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.UTC)
    return timestamp.astimezone(UK_TIMEZONE).date()


def _action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""

    action = {
        "BUY - MARKET": ActionType.BUY,
        "BUY - LIMIT": ActionType.BUY,
        "SELL - MARKET": ActionType.SELL,
        "SELL - LIMIT": ActionType.SELL,
        "TRANSFER FROM REVOLUT BANK UAB TO REVOLUT SECURITIES EUROPE UAB": ActionType.TRANSFER,
        "TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB": ActionType.TRANSFER,
        "STOCK SPLIT": ActionType.STOCK_SPLIT,
        "DIVIDEND": ActionType.DIVIDEND,
        "DIVIDEND TAX (CORRECTION)": ActionType.DIVIDEND_TAX,
        "CUSTODY FEE": ActionType.ADJUSTMENT,
        "CASH TOP-UP": ActionType.TRANSFER,
        "CASH WITHDRAWAL": ActionType.TRANSFER,
    }.get(label)
    if action is None:
        raise ParsingError(file, f"Unknown action: {label}")
    return action


@overload
def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[True],
    default: Decimal,
) -> Decimal: ...


@overload
def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[True],
    default: None = ...,
) -> Decimal | None: ...


@overload
def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[False],
    default: None = ...,
) -> Decimal: ...


def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: bool,
    default: Decimal | None = None,
) -> Decimal | None:
    """Parse decimal value from the row, raising ValueError with context on failure."""

    value = row[column]
    if value == "":
        if allow_empty:
            return default
        raise ValueError(f"Missing value in column '{column.value}'")

    normalized = value.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(
            f"Invalid decimal in column '{column.value}': {value!r}"
        ) from err


class RevolutTransaction(BrokerTransaction):
    """Represents a single Revolut transaction."""

    def __init__(
        self,
        row: dict[str, str],
        file: Path,
    ):
        """Create transaction from a CSV row keyed by column name."""
        # copied due to in-place removal of currency prefix from some columns
        row_values = dict(row)
        currency = CurrencyCode(row_values[RevolutColumn.CURRENCY])
        for money_column in (RevolutColumn.PRICE_PER_SHARE, RevolutColumn.TOTAL_AMOUNT):
            if row_values[money_column]:
                try:
                    _cur, row_values[money_column] = row_values[money_column].split(" ")
                except ValueError as err:
                    raise ParsingError(
                        file,
                        f"Expected format 'CURRENCY value' in '{money_column.value}' but found: {row_values[money_column]!r}",
                    ) from err
                _cur = CurrencyCode(_cur)
                if currency != _cur:
                    raise ParsingError(
                        file,
                        f"Expected currency for '{money_column.value}' to be '{currency}' but found '{_cur}'",
                    )
        date = _parse_date(row_values[RevolutColumn.DATE])
        description = row_values[RevolutColumn.ACTION]
        action = _action_from_str(description, file)
        symbol = row_values[RevolutColumn.TICKER] or None
        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        quantity = _parse_decimal(row_values, RevolutColumn.QUANTITY, allow_empty=True)
        price = _parse_decimal(
            row_values,
            RevolutColumn.PRICE_PER_SHARE,
            allow_empty=True,
            default=Decimal(0),
        )
        total = _parse_decimal(
            row_values, RevolutColumn.TOTAL_AMOUNT, allow_empty=False
        )
        if quantity:
            price = total / quantity  # override CSV's rounding
        amount = -total if action is ActionType.BUY else total
        super().__init__(
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            Decimal(0),
            amount,
            currency,
            "Revolut",
        )


class RevolutParser(StandardCSVParser[RevolutTransaction]):
    """Parser for Revolut format transaction files."""

    arg_name = "revolut"
    pretty_name = "Revolut format"
    format_name = "CSV"

    columns: ClassVar[set[str]] = set(COLUMNS)

    @staticmethod
    def _has_header(line: str) -> bool:
        """Return True if the first line is likely a Revolut header."""
        first_row = next(csv.reader([line]), [])
        if not first_row:
            return False
        return all(re.sub("\\s+", "", value).isalpha() for value in first_row)

    @classmethod
    @override
    def pre_reading(cls, file: TextIO, file_path: Path) -> tuple[Iterable[str], int]:
        """Supply the header row for an export that omits it.

        The columns are then assumed to be in the standard order. A supplied
        header is not part of the file, so it sits before its first line and
        the rows keep the physical line numbers they have in the export.
        """
        lines = iter(file)
        first_line = next(lines, None)
        if first_line is None:
            raise ParsingError(file_path, "Revolut CSV file is empty")
        if cls._has_header(first_line):
            return chain([first_line], lines), 1
        LOGGER.warning(
            "Revolut CSV file %s is missing header row. "
            "The header is required but will be inferred for now.",
            file_path,
        )
        return chain([HEADER_LINE, first_line], lines), 0

    @classmethod
    @override
    def read_row(
        cls, row: dict[str, str], file_path: Path
    ) -> RevolutTransaction | None:
        """Read a single Revolut transaction from a CSV row."""
        transaction = RevolutTransaction(row, file_path)
        if (
            transaction.action == ActionType.TRANSFER
            and transaction.description.startswith("TRANSFER FROM REVOLUT")
        ):
            LOGGER.debug("Skipping %s", transaction.description)
            return None
        return transaction
