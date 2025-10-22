"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Final, Literal, overload

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

if TYPE_CHECKING:
    from pathlib import Path


class RawColumn(StrEnum):
    """Column names for the RAW format."""

    DATE = "date"
    ACTION = "action"
    SYMBOL = "symbol"
    QUANTITY = "quantity"
    PRICE = "price"
    FEES = "fees"
    CURRENCY = "currency"


COLUMNS: Final[list[str]] = [column.value for column in RawColumn]
CSV_COLUMNS_NUM: Final = len(COLUMNS)
LOGGER = logging.getLogger(__name__)


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""
    try:
        return ActionType[label.upper()]
    except KeyError as err:
        raise ParsingError(file, f"Unknown action: {label}") from err


@overload
def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
    *,
    allow_empty: Literal[True],
    default: Decimal,
) -> Decimal: ...


@overload
def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
    *,
    allow_empty: Literal[True],
    default: None = ...,
) -> Decimal | None: ...


@overload
def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
    *,
    allow_empty: Literal[False],
    default: None = ...,
) -> Decimal: ...


def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
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


def _validate_header(header: list[str], file: Path) -> None:
    """Validate optional header row."""

    if len(header) != CSV_COLUMNS_NUM:
        raise UnexpectedColumnCountError(header, CSV_COLUMNS_NUM, file)

    normalized = [value.strip().lower() for value in header]
    for index, (exp, act) in enumerate(zip(COLUMNS, normalized, strict=True), start=1):
        if exp != act:
            raise ParsingError(
                file,
                f"Expected column {index} to be '{exp}' but found '{header[index - 1]}'",
            )


def _has_header(first_row: list[str]) -> bool:
    """Return True if the first row is likely a RAW header."""

    if not first_row:
        return False
    return all(value.strip() != "" and value.strip().isalpha() for value in first_row)


class RawTransaction(BrokerTransaction):
    """Represents a single raw transaction.

    Example format:
    2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD
    2022-11-14,SELL,META,19,116.00,0.05,USD
    2022-08-15,BUY,META,105,180.50,0.00,USD
    2022-07-26,DIVIDEND,OTGLY,305,0.031737,0.0,USD
    2022-06-06,STOCK_SPLIT,AMZN,209,0.00,0.00,USD

    See tests/raw/data/test_data.csv for a sample file showing the expected format.
    """

    def __init__(
        self,
        row: list[str],
        file: Path,
    ):
        """Create transaction from CSV row."""
        if len(row) != CSV_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, CSV_COLUMNS_NUM, file)

        row_values: dict[RawColumn, str] = {
            column: row[i] for i, column in enumerate(RawColumn)
        }

        date_str = row_values[RawColumn.DATE]
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        action = action_from_str(row_values[RawColumn.ACTION], file)
        symbol = row_values[RawColumn.SYMBOL] or None

        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        quantity = _parse_decimal(row_values, RawColumn.QUANTITY, allow_empty=True)
        price = _parse_decimal(row_values, RawColumn.PRICE, allow_empty=True)
        fees = _parse_decimal(
            row_values,
            RawColumn.FEES,
            allow_empty=True,
            default=Decimal(0),
        )

        if price is not None and quantity is not None:
            amount = price * quantity

            if action is ActionType.BUY:
                amount = -abs(amount)
            amount -= fees
        else:
            amount = None

        currency = row_values[RawColumn.CURRENCY]
        broker = "Unknown"
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


def read_raw_transactions(transactions_file: Path) -> list[BrokerTransaction]:
    """Read Raw transactions from file."""
    with transactions_file.open(encoding="utf-8") as csv_file:
        print(f"Parsing {transactions_file}...")
        lines = list(csv.reader(csv_file))

    if not lines:
        raise ParsingError(transactions_file, "RAW CSV file is empty")

    data_rows = lines
    start_index = 1
    if _has_header(lines[0]):
        _validate_header(lines[0], transactions_file)
        data_rows = lines[1:]
        start_index = 2
    else:
        LOGGER.warning(
            "RAW CSV file %s is missing header row. The header is required but will be inferred for now.",
            transactions_file,
        )

    transactions: list[BrokerTransaction] = []
    for index, row in enumerate(data_rows, start=start_index):
        try:
            transactions.append(RawTransaction(row, transactions_file))
        except ParsingError as err:
            err.add_row_context(index)
            raise
        except ValueError as err:
            raise ParsingError(transactions_file, str(err), row_index=index) from err

    if len(transactions) == 0:
        LOGGER.warning("No transactions detected in file %s", transactions_file)
    return transactions
