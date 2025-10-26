"""Exceptions to different errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime
    from decimal import Decimal
    from pathlib import Path

    from .model import BrokerTransaction


class CgtError(Exception):
    """Base class for exceptions."""


class ParsingError(CgtError):
    """Parsing error."""

    def __init__(
        self, file: Path, message: str, *, row_index: int | None = None
    ) -> None:
        """Initialise."""
        self.file = file
        self.detail = message
        self.row_index = row_index
        self._update_message()
        super().__init__(self.message)

    def _update_message(self) -> None:
        location = f"While parsing {self.file}"
        if self.row_index is not None:
            location += f", row {self.row_index}"
        self.message = f"{location}: {self.detail}"
        self.args = (self.message,)

    def add_row_context(self, row_index: int) -> None:
        """Attach the row number to the error context."""
        self.row_index = row_index
        self._update_message()


class UnsupportedBrokerActionError(ParsingError):
    """Raised when a broker export contains an unsupported action."""

    def __init__(self, file: Path, broker: str, action: str):
        """Initialise."""
        message = (
            f"Unsupported {broker} action '{action}'. "
            "Please check if a newer version of cgt-calc adds support or open an issue."
        )
        super().__init__(file, message)


class UnsupportedBrokerCurrencyError(ParsingError):
    """Raised when a broker export uses an unsupported account currency."""

    def __init__(self, file: Path, broker: str, currency: str):
        """Initialise."""
        super().__init__(
            file,
            f"{broker} parser does not support the provided account currency: {currency}.",
        )


class InvalidTransactionError(CgtError):
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

    def __init__(self, row: list[str], count: int, file: Path):
        """Initialise."""
        super().__init__(
            file, f"The following row doesn't have {count} columns:\n{row}"
        )


class UnexpectedRowCountError(ParsingError):
    """Unexpected row error."""

    def __init__(self, count: int, file: Path):
        """Initialise."""
        super().__init__(file, f"The following file doesn't have {count} rows:")


class CalculatedAmountDiscrepancyError(InvalidTransactionError):
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


class CalculationError(CgtError):
    """Calculation error."""


class ExchangeRateMissingError(CalculationError):
    """Exchange rate is missing error."""

    def __init__(self, symbol: str, date: datetime.date):
        """Initialise."""
        self.message = f"No GBP/{symbol} price for {date}"
        super().__init__(self.message)


class LatexRenderError(CgtError):
    """Raised when LaTeX PDF rendering fails."""

    def __init__(self, log_path: Path) -> None:
        """Initialise."""
        super().__init__(f"LaTeX compilation failed: see '{log_path}'")


class MissingExternalToolError(CgtError):
    """Raised when a required external command-line tool is missing."""

    def __init__(self, tool: str):
        """Initialise."""
        super().__init__(f"Required tool '{tool}' is not available on PATH")


class IsinTranslationError(CgtError):
    """Raised when invalid ISIN translation data is encountered."""

    def __init__(self, message: str):
        """Initialise."""
        self.message = message
        super().__init__(self.message)


class ExternalApiError(CgtError):
    """Raised when an external API request fails or returns invalid data."""

    def __init__(self, url: str, message: str):
        """Initialise."""
        super().__init__(f"{message} (source: {url})")
