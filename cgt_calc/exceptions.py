"""Exceptions to different errors."""
from __future__ import annotations

import datetime
from decimal import Decimal

from .model import BrokerTransaction


class ParsingError(Exception):
    """Parsing error."""

    def __init__(self, file: str, message: str):
        """Initialise."""
        self.message = f"While parsing {file}, {message}"
        super().__init__(self.message)


class InvalidTransactionError(Exception):
    """Invalid transaction error."""

    def __init__(self, transaction: BrokerTransaction, message: str):
        """Initialise."""
        self.transaction = transaction
        self.message = f"{message} for the following transaction:\n{transaction}"
        super().__init__(self.message)


class AmountMissingError(InvalidTransactionError):
    """Amount is missing error."""

    def __init__(self, transaction: BrokerTransaction):
        """Initialise."""
        super().__init__(transaction, "Amount missing")


class SymbolMissingError(InvalidTransactionError):
    """Symbol is missing error."""

    def __init__(self, transaction: BrokerTransaction):
        """Initialise."""
        super().__init__(transaction, "Symbol missing")


class PriceMissingError(InvalidTransactionError):
    """Price is missing error."""

    def __init__(self, transaction: BrokerTransaction):
        """Initialise."""
        super().__init__(transaction, "Price missing")


class QuantityNotPositiveError(InvalidTransactionError):
    """Quantity is negative error."""

    def __init__(self, transaction: BrokerTransaction):
        """Initialise."""
        super().__init__(transaction, "Positive quantity required")


class UnexpectedColumnCountError(ParsingError):
    """Unexpected column error."""

    def __init__(self, row: list[str], count: int, file: str):
        """Initialise."""
        super().__init__(
            file, f"The following row doesn't have {count} columns:\n{row}"
        )


class CalculatedAmountDiscrepancy(InvalidTransactionError):
    """Calculated amount discrepancy error."""

    def __init__(self, transaction: BrokerTransaction, calculated_amount: Decimal):
        """Initialise."""
        super().__init__(
            transaction,
            (
                f"Calculated amount({calculated_amount}) differs "
                f"from supplied amount ({transaction.amount})"
            ),
        )


class CalculationError(Exception):
    """Calculation error."""


class ExchangeRateMissingError(CalculationError):
    """Exchange rate is missing error."""

    def __init__(self, symbol: str, date: datetime.date):
        """Initialise."""
        self.message = f"No GBP/{symbol} price for {date}"
        super().__init__(self.message)
