"""Functions to work with HMRC transaction log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import ExcessReportedIncome, HmrcTransactionData, HmrcTransactionLog

if TYPE_CHECKING:
    import datetime
    from decimal import Decimal


def has_key(
    transactions: HmrcTransactionLog,
    date_index: datetime.date,
    symbol: str,
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
    eris: list[ExcessReportedIncome] | None = None,
) -> None:
    """Add entry to given transaction log."""
    current_list.setdefault(date_index, {})
    current_list[date_index].setdefault(symbol, HmrcTransactionData())
    current_list[date_index][symbol] += HmrcTransactionData(
        quantity=quantity, amount=amount, fees=fees, eris=eris or []
    )
