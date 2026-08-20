"""Hargreaves Lansdown parser."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import re
from typing import ClassVar, TextIO, override

import pdfplumber

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.base_parsers import BaseDirParser, StandardCSVParser

# PDF parser regexes
DATE_REGEX = re.compile(r"Date[^\d]*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
ACTION_REGEX = re.compile(r"\*\*\s*(BOUGHT|SOLD)\s*\*\*", re.IGNORECASE)
ISIN_TICKER_REGEX = re.compile(
    r"([A-Z]{2}[A-Z0-9]{9}\d)\s*STOCK CODE:\s*([A-Z0-9]+)", re.IGNORECASE
)
VALUES_REGEX = re.compile(
    r"([\d,]+\.\d{2,5})\s*(?:\|\s*)?([\d,]+\.\d{2,5})\s*(?:\|\s*)?([\d,]+\.\d{2,5})"
)
FEES_REGEX = re.compile(r"Dealing charge[^\d]*([\d,]+\.\d{2})", re.IGNORECASE)


@dataclass
class HLPdfData:
    """Data extracted from a single HL Contract Note PDF."""

    file_name: str
    date: date | None
    action: str | None
    ticker: str | None
    isin: str | None
    quantity: Decimal
    price: Decimal
    consideration: Decimal
    fees: Decimal


class HargreavesLansdownParser(StandardCSVParser, BaseDirParser):
    """Parser for Hargreaves Lansdown transaction exports and contract note PDFs."""

    arg_name = "hl"
    pretty_name = "Hargreaves Lansdown"
    format_name = "CSV and PDF"
    glob_dir = "*.csv"
    encoding = "windows-1252"
    deprecated_flags: ClassVar[list[str]] = []

    columns: ClassVar[set[str]] = {
        "Trade date",
        "Reference",
        "Description",
        "Value (£)",
    }
    optional_columns: ClassVar[set[str]] = {
        "Unit cost (p)",
        "Settle date",
        "Quantity",
        "",
    }

    @staticmethod
    def _determine_action_type(reference: str) -> ActionType | None:
        """Map Hargreaves Lansdown CSV reference codes to cgt-calc ActionTypes."""
        ref = reference.strip().upper()

        if ref.startswith("B") and ref[1:].isdigit():
            return ActionType.BUY
        if ref.startswith("S") and ref[1:].isdigit():
            return ActionType.SELL
        if ref == "INTEREST":
            return ActionType.INTEREST
        if ref in ("FPC", "MANAGE FEE"):
            return ActionType.TRANSFER

        return None

    @staticmethod
    def _parse_pdf(pdf_path: Path) -> HLPdfData:
        """Extract precise execution data from a single HL Contract Note PDF."""
        text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"

        # 1. Tolerate any non-digit characters between "Date" and the actual date
        date_match = DATE_REGEX.search(text)
        action_match = ACTION_REGEX.search(text)
        isin_ticker_match = ISIN_TICKER_REGEX.search(text)

        # 2. Look for three sequential floats (Qty, Price, Consideration)
        # separated by any whitespace or optional pipes
        values_match = VALUES_REGEX.search(text)

        # 3. Tolerate any non-digit characters between "Dealing charge" and its numeric value
        fees_match = FEES_REGEX.search(text)

        date_str = date_match.group(1) if date_match else None

        quantity = price = consideration = Decimal(0)
        if values_match:
            quantity = Decimal(values_match.group(1).replace(",", ""))
            price = Decimal(values_match.group(2).replace(",", ""))
            consideration = Decimal(values_match.group(3).replace(",", ""))

        fees = (
            Decimal(fees_match.group(1).replace(",", "")) if fees_match else Decimal(0)
        )

        return HLPdfData(
            file_name=pdf_path.name,
            date=datetime.strptime(date_str, "%d/%m/%Y").date() if date_str else None,
            action=action_match.group(1).lower() if action_match else None,
            ticker=isin_ticker_match.group(2) if isin_ticker_match else None,
            isin=isin_ticker_match.group(1) if isin_ticker_match else None,
            quantity=quantity,
            price=price,
            consideration=consideration,
            fees=fees,
        )

    @classmethod
    @override
    def pre_reading(cls, file: TextIO, file_path: Path) -> Iterable[str]:
        """Skip preamble lines until the 'Trade date' header is found."""
        lines = iter(file)
        for line in lines:
            if line.strip().startswith("Trade date"):
                yield line
                yield from lines
                return

        raise ParsingError(file_path, "Could not find the 'Trade date' header.")

    @classmethod
    @override
    def read_row(cls, row: dict[str, str], file_path: Path) -> BrokerTransaction | None:
        """Read a single transaction from a CSV row and cross-reference with PDFs."""
        reference = row.get("Reference", "").strip()
        if not reference:
            return None

        description = row.get("Description", "")
        action_type = cls._determine_action_type(reference)
        if not action_type:
            raise ParsingError(
                file_path,
                f"Unknown transaction type for reference '{reference}' "
                f"(description: '{description}')",
            )

        date_str = row.get("Trade date", "")
        try:
            trade_date = datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError as err:
            raise ParsingError(file_path, f"Invalid date format: {date_str}") from err

        quantity = None
        price = None
        fees = Decimal(0)
        symbol = None
        isin = None

        amount_str = row.get("Value (£)", "0").replace(",", "")
        amount = (
            Decimal(amount_str) if amount_str and amount_str != "n/a" else Decimal(0)
        )

        # Live PDF lookup only when needed for buy/sell actions
        if action_type in [ActionType.BUY, ActionType.SELL]:
            matching_pdfs = list(file_path.parent.glob(f"{reference}_*.pdf"))
            if matching_pdfs:
                pdf = cls._parse_pdf(matching_pdfs[0])
                symbol = pdf.ticker
                isin = pdf.isin
                quantity = pdf.quantity
                price = pdf.price / Decimal(100)
                fees = pdf.fees
            else:
                raise ParsingError(
                    file_path,
                    f"Cannot find contract note pdf for transaction {reference}",
                )

        return BrokerTransaction(
            date=trade_date,
            action=action_type,
            symbol=symbol,
            description=description,
            quantity=quantity,
            price=price,
            fees=fees,
            amount=amount,
            currency="GBP",
            broker=cls.pretty_name,
            isin=isin,
        )

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read transactions for a single file and reverse them independently to preserve correct order for same date operations."""
        transactions = super().read_transactions(file, file_path)
        transactions.reverse()
        return transactions

    @classmethod
    @override
    def post_process_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Sort all combined transactions globally by date to ensure correct relative order across files."""
        transactions.sort(key=lambda t: t.date)
        return transactions
