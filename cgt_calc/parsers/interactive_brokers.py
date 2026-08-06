"""Interactive brokers transaction parser."""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from itertools import chain
import logging
import re
from typing import TYPE_CHECKING, ClassVar, Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.util import is_isin

from .base_parsers import StandardCSVParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


LOGGER = logging.getLogger(__name__)

EXPECTED_COLS_IN_SUMMARY_SECTION: Final[int] = 4

# Dividend and withholding rows name the security as "VT(US9220427424) Cash
# Dividend ...". Trade rows carry a plain description, so this is best effort.
_ISIN_IN_DESCRIPTION_RE: Final = re.compile(r"^[^\s(]+\((?P<isin>[A-Z0-9]{12})\)")


def _isin_from_description(description: str) -> str | None:
    """Return the ISIN the description is prefixed with, if it has one."""
    match = _ISIN_IN_DESCRIPTION_RE.match(description)
    if match is None:
        return None
    isin = match.group("isin")
    return isin if is_isin(isin) else None


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
    PRICE_CURRENCY = "Price Currency"
    GROSS_AMOUNT = "Gross Amount "
    COMMISSION = "Commission"
    NET_AMOUNT = "Net Amount"
    EXCHANGE_RATE = "Exchange Rate"


_IBKR_OPTIONAL_COLUMNS: Final[set[str]] = set(
    {
        InteractiveBrokersColumn.PRICE_CURRENCY,
        InteractiveBrokersColumn.EXCHANGE_RATE,
    }
)


def _action_from_str(action_type: str, file_path: Path) -> ActionType:
    """Infer action type."""
    if action_type == "Credit Interest":
        return ActionType.INTEREST
    # A payment in lieu arrives instead of a dividend when the holding is out on
    # loan. It is the rarer of the two, so a parser that knows it and not the
    # ordinary dividend fails for most holders on their first real statement.
    if action_type in ["Dividend", "Payment in Lieu"]:
        return ActionType.DIVIDEND
    if action_type in ["Deposit", "Withdrawal"]:
        return ActionType.TRANSFER
    if action_type == "Buy":
        return ActionType.BUY
    if action_type == "Sell":
        return ActionType.SELL
    if action_type == "Foreign Tax Withholding":
        return ActionType.DIVIDEND_TAX
    if action_type in ["Forex Trade Component", "Other Fee"]:
        return ActionType.FEE
    # "FX Translations P&L": the revaluation of a foreign currency balance, not
    # a trade. It moves the cash balance and creates no taxable event, which is
    # what ADJUSTMENT means here and how the Schwab parser treats its own.
    if action_type == "Adjustment":
        return ActionType.ADJUSTMENT

    raise ParsingError(file_path, f"Unknown type: '{action_type}'")


def _parse_str(row: dict[str, str], column: str) -> str | None:
    """Read a text column, treating IBKR's "-" placeholder as absent.

    IBKR writes "-" wherever a column does not apply to a row, in text columns
    as well as numeric ones. Taking it at face value puts a currency called "-"
    on the transaction.
    """
    value = row.get(column)
    if not value or value == "-":
        return None
    return value


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
        price_currency = (
            _parse_str(row, InteractiveBrokersColumn.PRICE_CURRENCY) or "GBP"
        )
        exchange_rate = (
            _parse_decimal(row, InteractiveBrokersColumn.EXCHANGE_RATE)
            if InteractiveBrokersColumn.EXCHANGE_RATE in row
            else Decimal(1)
        )

        # The Gross/Net Amount and Commission columns are always in the account's base
        # currency (GBP). When the Price is in a foreign currency, convert it to GBP so
        # that the internal validation (quantity x price + fees ≈ |amount|) holds.
        if price is not None and price_currency != "GBP" and exchange_rate is not None:
            price = price * exchange_rate
            price_currency = "GBP"

        super().__init__(
            date=date,
            action=action,
            symbol=symbol,
            description=row[InteractiveBrokersColumn.DESCRIPTION],
            quantity=abs(quantity) if quantity is not None else None,
            price=price,
            fees=-fees,
            amount=amount,
            currency=price_currency,
            broker="Interactive Brokers",
            isin=_isin_from_description(row[InteractiveBrokersColumn.DESCRIPTION]),
        )


class InteractiveBrokersParser(StandardCSVParser):
    """Parser for RAW format transaction files."""

    arg_name = "interactive-brokers"
    pretty_name = "Interactive Brokers"
    format_name = "CSV"
    optional_columns: ClassVar[set[str]] = _IBKR_OPTIONAL_COLUMNS
    columns: ClassVar[set[str]] = {
        column.value
        for column in InteractiveBrokersColumn
        if column.value not in _IBKR_OPTIONAL_COLUMNS
    }

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
                    len(rows) < EXPECTED_COLS_IN_SUMMARY_SECTION
                    or rows[3].strip() != "GBP"
                ):
                    raise ParsingError(
                        file_path,
                        f"Unexpected base currency: '{rows[3]}', only GBP is supported",
                    )
            if line.startswith("Transaction History"):
                return chain([line], file)
        raise ParsingError(
            file_path,
            "Couldn't find Transaction History header, is this the right file?",
        )
