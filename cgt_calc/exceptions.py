"""Exception types for the various error conditions."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .util import strip_zeros

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


class UnclassifiedGiftError(CgtError):
    """Raised when a broker reports a gift of shares without saying who got them.

    Deliberately not an InvalidTransactionError: the transaction is perfectly
    valid and the message already names it, so dumping the row on the end only
    buries the instructions.
    """

    def __init__(self, transaction: BrokerTransaction, readings: list[Decimal]):
        """Initialise.

        `readings` holds every share count the row could be stating. There is
        normally one; a count printed before a stock split can be two, where
        the export does not say whether it was restated.
        """
        self.transaction = transaction
        rows = "\n".join(
            f"  {transaction.date},TRANSFER_TO_SPOUSE,{transaction.symbol},"
            f"{strip_zeros(reading)},0.00,0.00,{transaction.currency}"
            for reading in readings
        )
        gift = (
            f"{strip_zeros(readings[0])} units of {transaction.symbol}"
            if len(readings) == 1
            else f"{transaction.symbol} shares"
        )
        spouse = (
            "If you gave them to a spouse or civil partner and the transfer "
            "qualifies under TCGA 1992 s58 (normally living together in that "
            "tax year, or within the window after separating), it is a no "
            "gain/no loss transfer."
        )
        if len(readings) == 1:
            how = (
                f"{spouse} Put this line in a CSV and pass it with --raw-file "
                "alongside the file you already use, and the gift is then taken "
                f"as accounted for:\n{rows}"
            )
        else:
            options = " or ".join(strip_zeros(reading) for reading in readings)
            how = (
                f"{spouse} You record that by putting a line in a CSV and "
                "passing it with --raw-file alongside the file you already "
                "use.\n"
                "\n"
                f"{transaction.symbol} split after this date and the export does "
                "not say whether it restated the count, so the gift was either "
                f"{options} shares. Check a statement to see which, and add that "
                f"line:\n{rows}"
            )
        self.message = (
            f"{transaction.broker} reported a gift of {gift} on "
            f"{transaction.date}, but not who received them, and that decides "
            "the tax.\n"
            "\n"
            f"{how}\n"
            "\n"
            "If they went to anyone else, it is a disposal at market value and "
            "may be chargeable. cgt-calc cannot work that out yet, so you have "
            "to do it by hand (consider professional advice).\n"
            "\n"
            "See https://cgt-calc.uk/brokers/raw/"
            "#transfers-to-a-spouse-or-civil-partner"
        )
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


class QuantityMissingError(InvalidTransactionError):
    """Quantity is missing error."""

    def __init__(self, transaction: BrokerTransaction):
        """Initialise."""
        super().__init__(transaction, "Quantity missing")


class QuantityNotPositiveError(InvalidTransactionError):
    """Quantity is not positive error."""

    def __init__(self, transaction: BrokerTransaction):
        """Initialise."""
        super().__init__(transaction, "Positive quantity required")


class UnexpectedColumnCountError(ParsingError):
    """Unexpected column error."""

    def __init__(
        self, row: list[str], count: int, file: Path, *, row_index: int | None = None
    ):
        """Initialise."""
        super().__init__(
            file,
            f"The following row doesn't have {count} columns:\n{row}",
            row_index=row_index,
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


class InitialPriceMissingError(CalculationError):
    """Initial stock price is missing error."""

    def __init__(self, symbol: str, date: datetime.date):
        """Initialise."""
        self.message = (
            f"No initial price for {symbol} on {date}, add it via --initial-prices-file"
        )
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


class InteractiveInputRequiredError(CgtError):
    """Raised when a prompt is needed but stdin is not a terminal."""

    def __init__(self, symbol: str, date: datetime.date, spin_offs_file: Path):
        """Initialise."""
        super().__init__(
            f"Cannot ask which stock {symbol} was spun off from on {date}: "
            "standard input is not a terminal (this also applies when piping "
            f"transactions via '-'). Add a '{symbol},<source>' row to "
            f"{spin_offs_file} (header 'dst,src') and rerun."
        )


class MarketDataMissingError(CgtError):
    """Raised when the market data provider has no data for a symbol and date."""

    def __init__(self, symbol: str, date: datetime.date):
        """Initialise."""
        super().__init__(
            f"No market data found for {symbol} around {date}. The ticker may "
            "have been renamed or delisted; consider providing the price via "
            "--initial-prices-file."
        )
