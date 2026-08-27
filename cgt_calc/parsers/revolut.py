"""Revolut transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
import re
from typing import TYPE_CHECKING, Final, Literal, TextIO, overload, override

from cgt_calc.const import TICKER_RENAMES, UK_TIMEZONE
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
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
CSV_COLUMNS_NUM: Final = len(COLUMNS)
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
    row: dict[RevolutColumn, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[True],
    default: Decimal,
) -> Decimal: ...


@overload
def _parse_decimal(
    row: dict[RevolutColumn, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[True],
    default: None = ...,
) -> Decimal | None: ...


@overload
def _parse_decimal(
    row: dict[RevolutColumn, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[False],
    default: None = ...,
) -> Decimal: ...


def _parse_decimal(
    row: dict[RevolutColumn, str],
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
        row: list[str],
        file: Path,
    ):
        """Create transaction from CSV row."""
        if len(row) != CSV_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, CSV_COLUMNS_NUM, file)

        row_values: dict[RevolutColumn, str] = {
            column: row[i] for i, column in enumerate(RevolutColumn)
        }
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


class RevolutParser(BaseSingleFileParser):
    """Parser for Revolut format transaction files."""

    arg_name = "revolut"
    pretty_name = "Revolut format"
    format_name = "CSV"

    @staticmethod
    def _validate_header(header: list[str], file: Path) -> None:
        """Validate optional header row."""

        if len(header) != CSV_COLUMNS_NUM:
            raise UnexpectedColumnCountError(header, CSV_COLUMNS_NUM, file, row_index=1)

        for index, (exp, act) in enumerate(zip(COLUMNS, header, strict=True), start=1):
            if exp != act:
                raise ParsingError(
                    file,
                    f"Expected column {index} to be '{exp}' but found '{header[index - 1]}' ('{act}')",
                    row_index=1,
                )

    @staticmethod
    def _has_header(first_row: list[str]) -> bool:
        """Return True if the first row is likely a Revolut header."""
        if not first_row:
            return False
        return all(re.sub("\\s+", "", value).isalpha() for value in first_row)

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read Revolut transactions from file."""
        lines = list(csv.reader(file))

        if not lines:
            raise ParsingError(file_path, "Revolut CSV file is empty")

        data_rows = lines
        start_index = 1
        if cls._has_header(lines[0]):
            cls._validate_header(lines[0], file_path)
            data_rows = lines[1:]
            start_index = 2
        else:
            LOGGER.warning(
                "Revolut CSV file %s is missing header row. The header is required but will be inferred for now.",
                file_path,
            )

        transactions: list[BrokerTransaction] = []
        for index, row in enumerate(data_rows, start=start_index):
            try:
                new = RevolutTransaction(row, file_path)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
            if new.action == ActionType.TRANSFER and new.description.startswith(
                "TRANSFER FROM REVOLUT"
            ):
                LOGGER.debug("Skipping row %d: %s", index, new.description)
            else:
                transactions.append(new)

        return transactions
