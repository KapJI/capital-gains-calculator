"""Functions to work with HMRC transaction log."""
from dataclasses import astuple
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
    if date_index not in current_list:
        current_list[date_index] = {}
    if symbol not in current_list[date_index]:
        current_list[date_index][symbol] = HmrcTransactionData(
            quantity=Decimal(0), amount=Decimal(0), fees=Decimal(0)
        )
    current_quantity, current_amount, current_fees = astuple(
        current_list[date_index][symbol]
    )
    current_list[date_index][symbol] = HmrcTransactionData(
        quantity=current_quantity + quantity,
        amount=current_amount + amount,
        fees=current_fees + fees,
    )
