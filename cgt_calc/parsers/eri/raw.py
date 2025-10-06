"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from importlib.abc import Traversable
    from pathlib import Path

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError

from .model import EriTransaction

COLUMNS: Final[list[str]] = [
    "ISIN",
    "Fund Reporting Period End Date",
    "Currency",
    "Excess of reporting income over distribution",
]


class EriRaw(EriTransaction):
    """Represents a single raw ERI transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: str):
        """Create transaction from CSV row."""
        if len(row_raw) != len(COLUMNS):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), file)

        row = dict(zip(header, row_raw, strict=False))

        isin = row["ISIN"]
        date = datetime.datetime.strptime(
            row["Fund Reporting Period End Date"], "%d/%m/%Y"
        ).date()
        currency = row["Currency"]
        price = Decimal(row["Excess of reporting income over distribution"])

        super().__init__(
            date,
            isin,
            price,
            currency,
        )


def validate_header(header: list[str], filename: str, columns: list[str]) -> None:
    """Check if header is valid."""
    for actual in header:
        if actual not in columns:
            msg = f"Unknown column {actual}"
            raise ParsingError(filename, msg)


def read_eri_raw(
    eri_file: Path | Traversable,
) -> list[EriTransaction]:
    """Read ERI raw transactions from file."""

    transactions: list[EriTransaction] = []
    try:
        with eri_file.open(encoding="utf-8") as csv_file:
            lines = list(csv.reader(csv_file))

            header = lines[0]
            validate_header(header, eri_file.name, COLUMNS)

            lines = lines[1:]
            cur_transactions = [EriRaw(header, row, eri_file.name) for row in lines]
            if len(cur_transactions) == 0:
                print(f"WARNING: no transactions detected in file {eri_file}")
            transactions += cur_transactions

    except FileNotFoundError:
        print(f"WARNING: Couldn't locate ERI raw file({eri_file})")
        return []

    return transactions
