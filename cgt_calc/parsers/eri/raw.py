"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
from importlib import resources
import logging
from typing import TYPE_CHECKING, Final, TextIO

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction

from cgt_calc.const import ERI_RESOURCE_FOLDER
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.parsers.base_parsers import BaseSingleFileParser
from cgt_calc.resources import RESOURCES_PACKAGE
from cgt_calc.util import is_isin

from .model import EriTransaction

COLUMNS: Final[list[str]] = [
    "ISIN",
    "Fund Reporting Period End Date",
    "Currency",
    "Excess of reporting income over distribution",
]
LOGGER = logging.getLogger(__name__)


class EriRaw(EriTransaction):
    """Represents a single raw ERI transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: Path) -> None:
        """Create transaction from CSV row."""
        if len(row_raw) != len(COLUMNS):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), file)

        row = dict(zip(header, row_raw, strict=True))

        isin = row["ISIN"]
        if not is_isin(isin):
            raise ParsingError(file, f"Invalid ISIN value '{isin}' in ERI data")
        date_str = row["Fund Reporting Period End Date"]
        try:
            date = datetime.datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError as err:
            raise ParsingError(file, f"Invalid date '{date_str}' in ERI data") from err
        currency = row["Currency"]
        price_str = row["Excess of reporting income over distribution"]
        try:
            price = Decimal(price_str)
        except (InvalidOperation, ValueError) as err:
            raise ParsingError(
                file, f"Invalid decimal '{price_str}' in ERI data"
            ) from err

        super().__init__(
            date,
            isin,
            price,
            currency,
        )


class ERIRawParser(BaseSingleFileParser):
    """Parser for RAW format transaction files."""

    arg_name = "eri-raw"
    pretty_name = "Historical Excess Reported Income data"
    format_name = "CSV"

    @staticmethod
    def _validate_header(header: list[str], file: Path, columns: list[str]) -> None:
        """Check if header is valid."""
        for actual in header:
            if actual not in columns:
                msg = f"Unknown column {actual}"
                raise ParsingError(file, msg)

    @classmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load ERI data from arguments and pre-packaged data."""
        transactions: list[BrokerTransaction] = []
        for entry in (
            resources.files(RESOURCES_PACKAGE).joinpath(ERI_RESOURCE_FOLDER).iterdir()
        ):
            if entry.is_file() and entry.name.endswith(".csv"):
                with resources.as_file(entry) as path:
                    transactions += cls.load_from_file(path, show_parsing_msg=False)
        transactions += super().load_from_args(args)
        return transactions

    @classmethod
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read ERI raw transactions from file."""

        lines = list(csv.reader(file))

        if not lines:
            raise ParsingError(file_path, "ERI data file is empty")

        header = lines[0]

        ERIRawParser._validate_header(header, file_path, COLUMNS)

        lines = lines[1:]
        return [EriRaw(header, row, file_path) for row in lines]
