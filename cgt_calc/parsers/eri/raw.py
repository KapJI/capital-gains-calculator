"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.util import is_isin

from .model import EriTransaction

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

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


def validate_header(header: list[str], file: Path, columns: list[str]) -> None:
    """Check if header is valid."""
    for actual in header:
        if actual not in columns:
            msg = f"Unknown column {actual}"
            raise ParsingError(file, msg)


def read_eri_raw(
    eri_file: Path | Traversable,
) -> list[EriTransaction]:
    """Read ERI raw transactions from file."""
    transactions: list[EriTransaction] = []

    file_label = (
        eri_file if isinstance(eri_file, Path) else Path("resources") / eri_file.name
    )

    with eri_file.open(encoding="utf-8") as csv_file:
        lines = list(csv.reader(csv_file))

    if not lines:
        raise ParsingError(file_label, "ERI data file is empty")

    header = lines[0]

    validate_header(header, file_label, COLUMNS)

    lines = lines[1:]
    cur_transactions = [EriRaw(header, row, file_label) for row in lines]
    if len(cur_transactions) == 0:
        LOGGER.warning("No transactions detected in file: %s", eri_file)
    transactions += cur_transactions

    return transactions
