"""Functions to work with HMRC transaction log."""

from __future__ import annotations

import datetime
from decimal import Decimal

from .model import HmrcTransactionData, HmrcTransactionLog


def has_key(
    transactions: HmrcTransactionLog, date_index: datetime.date, symbol: str
) -> bool:
    """Check if transaction log has entry for date_index and symbol."""
    return date_index in transactions and symbol in transactions[date_index]


def add_to_list(
    current_list: HmrcTransactionLog,
    date_index: datetime.date,
    symbol: str,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
) -> None:
    """Add entry to given transaction log."""
    current_list.setdefault(date_index, {})
    current_list[date_index].setdefault(symbol, HmrcTransactionData())
    current_list[date_index][symbol] += HmrcTransactionData(
        quantity=quantity,
        amount=amount,
        fees=fees,
    )

def multiply_entries(
    current_list: HmrcTransactionLog,
    symbol: str,
    multiple: Decimal,
    multiply_date: datetime.date | None = None
) -> None:
    for date, entries in current_list.items():
        if multiply_date is not None:
            assert multiply_date >= date
        entries[symbol].quantity *= multiple

