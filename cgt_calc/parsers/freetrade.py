"""Freetrade parser."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
import logging
from pathlib import Path
from typing import Final

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

LOGGER = logging.getLogger(__name__)

COLUMNS: Final[list[str]] = [
    "Title",
    "Type",
    "Timestamp",
    "Account Currency",
    "Total Amount",
    "Buy / Sell",
    "Ticker",
    "ISIN",
    "Price per Share in Account Currency",
    "Stamp Duty",
    "Quantity",
    "Venue",
    "Order ID",
    "Order Type",
    "Instrument Currency",
    "Total Shares Amount",
    "Price per Share",
    "FX Rate",
    "Base FX Rate",
    "FX Fee (BPS)",
    "FX Fee Amount",
    "Dividend Ex Date",
    "Dividend Pay Date",
    "Dividend Eligible Quantity",
    "Dividend Amount Per Share",
    "Dividend Gross Distribution Amount",
    "Dividend Net Distribution Amount",
    "Dividend Withheld Tax Percentage",
    "Dividend Withheld Tax Amount",
]


class FreetradeTransaction(BrokerTransaction):
    """Represents a single Freetrade transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: Path):
        """Create transaction from CSV row."""
        row = dict(zip(header, row_raw, strict=False))
        action = action_from_str(row["Type"], row["Buy / Sell"], file)

        symbol = row["Ticker"] if row["Ticker"] != "" else None
        if symbol is None and action not in [ActionType.TRANSFER, ActionType.INTEREST]:
            raise ParsingError(file, f"No symbol for action: {action}")

        # I believe GIA account at Freetrade can be only in GBP
        if row["Account Currency"] != "GBP":
            raise ParsingError(file, "Non-GBP accounts are unsupported")

        # Convert all numbers in GBP using Freetrade rates
        if action in [ActionType.SELL, ActionType.BUY]:
            quantity = Decimal(row["Quantity"])
            price = Decimal(row["Price per Share"])
            amount = Decimal(row["Total Shares Amount"])
            currency = row["Instrument Currency"]
            if currency != "GBP":
                fx_rate = Decimal(row["FX Rate"])
                price /= fx_rate
                amount /= fx_rate
            currency = "GBP"
        elif action == ActionType.DIVIDEND:
            # Total amount before US tax withholding
            amount = Decimal(row["Dividend Gross Distribution Amount"])
            quantity, price = None, None
            currency = row["Instrument Currency"]
            if currency != "GBP":
                # FX Rate is not defined for dividends,
                # but we can use base one as there's no fee
                amount /= Decimal(row["Base FX Rate"])
            currency = "GBP"
        elif action in [ActionType.TRANSFER, ActionType.INTEREST]:
            amount = Decimal(row["Total Amount"])
            quantity, price = None, None
            currency = "GBP"
        else:
            raise ParsingError(
                file, f"Numbers parsing unimplemented for action: {action}"
            )

        if row["Type"] == "FREESHARE_ORDER":
            price = Decimal(0)
            amount = Decimal(0)

        amount_negative = action == ActionType.BUY or row["Type"] == "WITHDRAWAL"
        if amount is not None and amount_negative:
            amount *= -1

        super().__init__(
            date=datetime.fromisoformat(row["Timestamp"]).date(),
            action=action,
            symbol=symbol,
            description=f"{row['Title']} {action}",
            quantity=quantity,
            price=price,
            fees=Decimal(0),  # not implemented
            amount=amount,
            currency=currency,
            broker="Freetrade",
        )


def action_from_str(type: str, buy_sell: str, file: Path) -> ActionType:
    """Infer action type."""
    if type == "INTEREST_FROM_CASH":
        return ActionType.INTEREST
    if type == "DIVIDEND":
        return ActionType.DIVIDEND
    if type in ["TOP_UP", "WITHDRAWAL"]:
        return ActionType.TRANSFER
    if type in ["ORDER", "FREESHARE_ORDER"]:
        if buy_sell == "BUY":
            return ActionType.BUY
        if buy_sell == "SELL":
            return ActionType.SELL

        raise ParsingError(file, f"Unknown buy_sell: '{buy_sell}'")

    raise ParsingError(file, f"Unknown type: '{type}'")


def validate_header(header: list[str], file: Path) -> None:
    """Check if header is valid."""
    for actual in header:
        if actual not in COLUMNS:
            raise ParsingError(file, f"Unknown column {actual}")


def read_freetrade_transactions(transactions_file: Path) -> list[BrokerTransaction]:
    """Parse Freetrade transactions from a CSV file."""
    with transactions_file.open(encoding="utf-8") as file:
        LOGGER.info("Parsing %s...", transactions_file)
        lines = list(csv.reader(file))
        header = lines[0]
        validate_header(header, transactions_file)
        lines = lines[1:]
        # HACK: reverse transactions to avoid negative balance issues
        # the proper fix would be to use datetime in BrokerTransaction
        lines.reverse()
        transactions: list[BrokerTransaction] = [
            FreetradeTransaction(header, row, transactions_file) for row in lines
        ]
        if len(transactions) == 0:
            LOGGER.warning("No transactions detected in file %s", transactions_file)
        return transactions
