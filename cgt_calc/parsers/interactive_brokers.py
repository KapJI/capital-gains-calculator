"""Interactive brokers transaction parser."""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import chain
import logging
from typing import TYPE_CHECKING, ClassVar, Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

from .base_parsers import StandardCSVParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


LOGGER = logging.getLogger(__name__)

EXPECTED_COLS_IN_SUMMARY_SECTION: Final[int] = 4


class InteractiveBrokersColumn(StrEnum):
    """Column names for the interactive brokers format."""

    TRANSACTION_HISTORY = "Transaction History"
    HEADER = "Header"
    DATE = "Date"
    ACCOUNT = "Account"
    DESCRIPTION = "Description"
    TRANSACTION_TYPE = "Transaction Type"
    SYMBOL = "Symbol"
    QUANTITY = "Quantity"
    PRICE = "Price"
    GROSS_AMOUNT = "Gross Amount "
    COMMISSION = "Commission"
    NET_AMOUNT = "Net Amount"


def _action_from_str(action_type: str, file_path: Path) -> ActionType:
    """Infer action type."""
    if action_type == "Credit Interest":
        return ActionType.INTEREST
    if action_type == "Payment in Lieu":
        return ActionType.DIVIDEND
    if action_type in ["Deposit", "Withdrawal"]:
        return ActionType.TRANSFER
    if action_type == "Buy":
        return ActionType.BUY
    if action_type == "Sell":
        return ActionType.SELL
    if action_type in ["Foreign Tax Withholding"]:
        return ActionType.DIVIDEND_TAX
    if action_type == "Forex Trade Component":
        return ActionType.FEE

    raise ParsingError(file_path, f"Unknown type: '{action_type}'")


def _parse_decimal(row: dict[str, str], column: str) -> Decimal | None:
    """Parse decimal value from the row, raising ValueError with context on failure."""

    value = row[column]
    if value == "-":
        return None

    normalized = value.replace(",", "")
    try:
        return Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(f"Invalid decimal in column '{column}': {value!r}") from err


class InteractiveBrokersTransaction(BrokerTransaction):
    """Interactive Brokers Transaction parser."""

    def __init__(
        self,
        row: dict[str, str],
        file_path: Path,
    ):
        """Create transaction from CSV row."""
        date_str = row[InteractiveBrokersColumn.DATE]
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        action = _action_from_str(
            row[InteractiveBrokersColumn.TRANSACTION_TYPE], file_path
        )
        symbol = row[InteractiveBrokersColumn.SYMBOL] or None

        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        quantity = _parse_decimal(row, InteractiveBrokersColumn.QUANTITY)
        price = _parse_decimal(row, InteractiveBrokersColumn.PRICE)
        amount = _parse_decimal(row, InteractiveBrokersColumn.NET_AMOUNT)
        fees = _parse_decimal(row, InteractiveBrokersColumn.COMMISSION) or Decimal(0)

        super().__init__(
            date=date,
            action=action,
            symbol=symbol,
            description=row[InteractiveBrokersColumn.DESCRIPTION],
            quantity=abs(quantity) if quantity is not None else None,
            price=price,
            fees=-fees,
            amount=amount,
            currency="GBP",
            broker="Interactive Brokers",
        )


class InteractiveBrokersParser(StandardCSVParser):
    """Parser for RAW format transaction files."""

    arg_name = "interactive-brokers"
    pretty_name = "Interactive Brokers"
    format_name = "CSV"
    columns: ClassVar[set[str]] = {column.value for column in InteractiveBrokersColumn}

    @classmethod
    def read_row(cls, row: dict[str, str], file_path: Path) -> BrokerTransaction | None:
        """Read a single transaction from a row in the CSV."""
        return InteractiveBrokersTransaction(row, file_path)

    @classmethod
    def pre_reading(cls, file: Iterable[str], file_path: Path) -> Iterable[str]:
        """Skip Statement and Summary sections. Transaction History is the important one."""
        for line in file:
            # Validate we're dealing with a GBP account
            if line.startswith("Summary,Data,Base Currency,"):
                rows = line.split(",")
                if (
                    len(rows) != EXPECTED_COLS_IN_SUMMARY_SECTION
                    or rows[3].strip() != "GBP"
                ):
                    raise ParsingError(
                        file_path,
                        f"Unexpected base currency: {rows[3]}, only GBP is supported",
                    )
            if line.startswith("Transaction History"):
                return chain([line], file)
        raise ParsingError(
            file_path,
            "Couldn't find Transaction History header, is this the right file?",
        )
