"""Freetrade parser."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

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

    def __init__(self, header: list[str], row_raw: list[str], filename: str):
        """Create transaction from CSV row."""
        row = dict(zip(header, row_raw))
        action = action_from_str(row["Type"], row["Buy / Sell"], filename)

        symbol = row["Ticker"] if row["Ticker"] != "" else None
        if symbol is None and action not in [ActionType.TRANSFER, ActionType.INTEREST]:
            raise ParsingError(filename, f"No symbol for action: {action}")

        # I believe GIA account at Freetrade can be only in GBP
        if row["Account Currency"] != "GBP":
            raise ParsingError(filename, "Non-GBP accounts are unsupported")

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
                filename, f"Numbers parsing unimplemented for action: {action}"
            )

        if row["Type"] == "FREESHARE_ORDER":
            price = Decimal(0)
            amount = Decimal(0)

        amount_negative = action == ActionType.BUY or row["Type"] == "WITHDRAWAL"
        if amount is not None and amount_negative:
            amount *= -1

        super().__init__(
            date=parse_date(row["Timestamp"]),
            action=action,
            symbol=symbol,
            description=f"{row['Title']} {action}",
            quantity=quantity,
            price=price,
            fees=Decimal("0"),  # not implemented
            amount=amount,
            currency=currency,
            broker="Freetrade",
        )


def action_from_str(type: str, buy_sell: str, filename: str) -> ActionType:
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

        raise ParsingError(filename, f"Unknown buy_sell: {buy_sell}")

    raise ParsingError(filename, f"Unknown type: {type}")


def validate_header(header: list[str], filename: str) -> None:
    """Check if header is valid."""
    for actual in header:
        if actual not in COLUMNS:
            msg = f"Unknown column {actual}"
            raise ParsingError(filename, msg)


@dataclass
class StockSplit:
    """Info about stock split."""

    symbol: str
    date: date
    factor: int


STOCK_SPLIT_INFO = [
    StockSplit(symbol="GOOGL", date=datetime(2022, 7, 18).date(), factor=20),
    StockSplit(symbol="TSLA", date=datetime(2022, 8, 25).date(), factor=3),
    StockSplit(symbol="NDAQ", date=datetime(2022, 8, 29).date(), factor=3),
]


# Pretend there was no split
def _handle_stock_split(transaction: BrokerTransaction) -> BrokerTransaction:
    for split in STOCK_SPLIT_INFO:
        if (
            transaction.symbol == split.symbol
            and transaction.action == ActionType.SELL
            and transaction.date > split.date
        ):
            if transaction.quantity:
                transaction.quantity /= split.factor
            if transaction.price:
                transaction.price *= split.factor

    return transaction


def read_freetrade_transactions(transactions_file: str) -> list[BrokerTransaction]:
    """Parse Freetrade transactions from a CSV file."""
    try:
        with Path(transactions_file).open(encoding="utf-8") as file:
            lines = list(csv.reader(file))
            header = lines[0]
            validate_header(header, str(file))
            lines = lines[1:]
            # HACK: reverse transactions to avoid negative balance issues
            # the proper fix would be to use datetime in BrokerTransaction
            lines.reverse()
            transactions: list[BrokerTransaction] = [
                _handle_stock_split(FreetradeTransaction(header, row, str(file)))
                for row in lines
            ]
            if len(transactions) == 0:
                print(f"WARNING: no transactions detected in file {file}")
            return transactions
    except FileNotFoundError:
        print(
            f"WARNING: Couldn't locate Freetrade transactions file({transactions_file})"
        )
        return []


def parse_date(iso_date: str) -> date:
    """Parse ISO 8601 date with Z for Python versions before 3.11."""

    # Replace 'Z' with '+00:00' to make it compatible
    iso_date = iso_date.replace("Z", "+00:00")

    # Parse the string with the adjusted format
    parsed_datetime = datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%S.%f%z")
    return parsed_datetime.date()
