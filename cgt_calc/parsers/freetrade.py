"""Freetrade parser."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Final

from cgt_calc.exceptions import (
    ParsingError,
    UnsupportedBrokerActionError,
    UnsupportedBrokerCurrencyError,
)
from cgt_calc.model import ActionType, BrokerTransaction

if TYPE_CHECKING:
    from pathlib import Path

BROKER_NAME: Final = "Freetrade"
LOGGER = logging.getLogger(__name__)


class FreetradeColumn(StrEnum):
    """Column names expected in Freetrade CSV exports."""

    TITLE = "Title"
    TYPE = "Type"
    TIMESTAMP = "Timestamp"
    ACCOUNT_CURRENCY = "Account Currency"
    TOTAL_AMOUNT = "Total Amount"
    BUY_SELL = "Buy / Sell"
    TICKER = "Ticker"
    ISIN = "ISIN"
    PRICE_PER_SHARE_ACCOUNT = "Price per Share in Account Currency"
    STAMP_DUTY = "Stamp Duty"
    QUANTITY = "Quantity"
    VENUE = "Venue"
    ORDER_ID = "Order ID"
    ORDER_TYPE = "Order Type"
    INSTRUMENT_CURRENCY = "Instrument Currency"
    TOTAL_SHARES_AMOUNT = "Total Shares Amount"
    PRICE_PER_SHARE = "Price per Share"
    FX_RATE = "FX Rate"
    BASE_FX_RATE = "Base FX Rate"
    FX_FEE_BPS = "FX Fee (BPS)"
    FX_FEE_AMOUNT = "FX Fee Amount"
    DIVIDEND_EX_DATE = "Dividend Ex Date"
    DIVIDEND_PAY_DATE = "Dividend Pay Date"
    DIVIDEND_ELIGIBLE_QUANTITY = "Dividend Eligible Quantity"
    DIVIDEND_AMOUNT_PER_SHARE = "Dividend Amount Per Share"
    DIVIDEND_GROSS_AMOUNT = "Dividend Gross Distribution Amount"
    DIVIDEND_NET_AMOUNT = "Dividend Net Distribution Amount"
    DIVIDEND_WITHHELD_PERCENTAGE = "Dividend Withheld Tax Percentage"
    DIVIDEND_WITHHELD_AMOUNT = "Dividend Withheld Tax Amount"


COLUMNS: Final[list[str]] = [column.value for column in FreetradeColumn]
REQUIRED_COLUMNS: Final[set[str]] = set(COLUMNS)


class FreetradeTransaction(BrokerTransaction):
    """Represents a single Freetrade transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: Path) -> None:
        """Create transaction from CSV row."""
        row = dict(zip(header, row_raw, strict=False))
        action = action_from_str(
            row[FreetradeColumn.TYPE], row[FreetradeColumn.BUY_SELL], file
        )

        symbol = (
            row[FreetradeColumn.TICKER] if row[FreetradeColumn.TICKER] != "" else None
        )
        if symbol is None and action not in [ActionType.TRANSFER, ActionType.INTEREST]:
            raise ParsingError(file, f"No symbol for action: {action}")

        # I believe GIA account at Freetrade can be only in GBP
        if row[FreetradeColumn.ACCOUNT_CURRENCY] != "GBP":
            raise UnsupportedBrokerCurrencyError(
                file, BROKER_NAME, row[FreetradeColumn.ACCOUNT_CURRENCY]
            )

        # Convert all numbers in GBP using Freetrade rates
        if action in [ActionType.SELL, ActionType.BUY]:
            quantity = _parse_decimal(row, FreetradeColumn.QUANTITY)
            price = _parse_decimal(row, FreetradeColumn.PRICE_PER_SHARE)
            amount = _parse_decimal(row, FreetradeColumn.TOTAL_SHARES_AMOUNT)
            currency = row[FreetradeColumn.INSTRUMENT_CURRENCY]
            if currency != "GBP":
                fx_rate = _parse_decimal(row, FreetradeColumn.FX_RATE)
                price /= fx_rate
                amount /= fx_rate
            currency = "GBP"
        elif action == ActionType.DIVIDEND:
            # Total amount before US tax withholding
            amount = _parse_decimal(row, FreetradeColumn.DIVIDEND_GROSS_AMOUNT)
            quantity, price = None, None
            currency = row[FreetradeColumn.INSTRUMENT_CURRENCY]
            if currency != "GBP":
                # FX Rate is not defined for dividends,
                # but we can use base one as there's no fee
                amount /= _parse_decimal(row, FreetradeColumn.BASE_FX_RATE)
            currency = "GBP"
        elif action in [ActionType.TRANSFER, ActionType.INTEREST]:
            amount = _parse_decimal(row, FreetradeColumn.TOTAL_AMOUNT)
            quantity, price = None, None
            currency = "GBP"
        else:
            raise UnsupportedBrokerActionError(
                file, BROKER_NAME, row[FreetradeColumn.TYPE]
            )

        if row[FreetradeColumn.TYPE] == "FREESHARE_ORDER":
            price = Decimal(0)
            amount = Decimal(0)

        amount_negative = (
            action == ActionType.BUY or row[FreetradeColumn.TYPE] == "WITHDRAWAL"
        )
        if amount is not None and amount_negative:
            amount *= -1

        super().__init__(
            date=datetime.fromisoformat(row[FreetradeColumn.TIMESTAMP]).date(),
            action=action,
            symbol=symbol,
            description=f"{row[FreetradeColumn.TITLE]} {action}",
            quantity=quantity,
            price=price,
            fees=Decimal(0),  # not implemented
            amount=amount,
            currency=currency,
            broker=BROKER_NAME,
        )


def action_from_str(action_type: str, buy_sell: str, file: Path) -> ActionType:
    """Infer action type."""
    if action_type == "INTEREST_FROM_CASH":
        return ActionType.INTEREST
    if action_type == "DIVIDEND":
        return ActionType.DIVIDEND
    if action_type in ["TOP_UP", "WITHDRAWAL"]:
        return ActionType.TRANSFER
    if action_type in ["ORDER", "FREESHARE_ORDER"]:
        if buy_sell == "BUY":
            return ActionType.BUY
        if buy_sell == "SELL":
            return ActionType.SELL

        raise ParsingError(file, f"Unknown buy_sell: '{buy_sell}'")

    raise ParsingError(file, f"Unknown type: '{action_type}'")


def validate_header(header: list[str], file: Path) -> None:
    """Check if header is valid."""
    provided = set(header)
    missing = REQUIRED_COLUMNS - provided
    if missing:
        missing_columns = ", ".join(sorted(missing))
        raise ParsingError(file, f"Missing columns: {missing_columns}")

    unknown = provided - REQUIRED_COLUMNS
    if unknown:
        unknown_columns = ", ".join(sorted(unknown))
        raise ParsingError(file, f"Unknown columns: {unknown_columns}")


def _parse_decimal(row: dict[str, str], column: FreetradeColumn) -> Decimal:
    """Parse Decimal value for column, raising ValueError with context on failure."""

    value = row[column]
    try:
        return Decimal(value)
    except InvalidOperation as err:
        raise ValueError(
            f"Invalid decimal in column '{column.value}': {value!r}"
        ) from err


def read_freetrade_transactions(transactions_file: Path) -> list[BrokerTransaction]:
    """Parse Freetrade transactions from a CSV file."""
    with transactions_file.open(encoding="utf-8") as file:
        LOGGER.info("Parsing %s...", transactions_file)
        lines = list(csv.reader(file))
        if not lines:
            raise ParsingError(transactions_file, "Freetrade CSV file is empty")
        header = lines[0]
        validate_header(header, transactions_file)
        lines = lines[1:]
        indexed_rows = list(enumerate(lines, start=2))
        # HACK: reverse transactions to avoid negative balance issues
        # the proper fix would be to use datetime in BrokerTransaction
        indexed_rows.reverse()
        transactions: list[BrokerTransaction] = []
        for index, row in indexed_rows:
            try:
                transactions.append(
                    FreetradeTransaction(header, row, transactions_file)
                )
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(
                    transactions_file, str(err), row_index=index
                ) from err
        if len(transactions) == 0:
            LOGGER.warning("No transactions detected in file %s", transactions_file)
        return transactions
