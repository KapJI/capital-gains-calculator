"""Tests for ERI raw parser error handling."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.raw import read_eri_raw

if TYPE_CHECKING:
    from pathlib import Path

HEADER = (
    "ISIN,Fund Reporting Period End Date,Currency,"
    "Excess of reporting income over distribution\n"
)
VALID_ISIN = "US5949181045"
INVALID_ISIN = "US1234567890"


def test_read_eri_raw_raises_on_invalid_date(tmp_path: Path) -> None:
    """Raise ParsingError when ERI date field is invalid."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{VALID_ISIN},32/13/2024,USD,1.23\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match="Invalid date '32/13/2024'"):
        read_eri_raw(file_path)


def test_read_eri_raw_raises_on_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when ERI decimal field cannot be parsed."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{VALID_ISIN},01/02/2024,USD,not-a-number\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match="Invalid decimal 'not-a-number'"):
        read_eri_raw(file_path)


def test_read_eri_raw_raises_on_empty_file(tmp_path: Path) -> None:
    """Raise ParsingError when ERI file has no header row."""
    file_path = tmp_path / "eri.csv"
    file_path.touch()

    with pytest.raises(ParsingError, match="ERI data file is empty"):
        read_eri_raw(file_path)


def test_read_eri_raw_parses_valid_row(tmp_path: Path) -> None:
    """Successfully parse a well-formed ERI CSV row."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{VALID_ISIN},01/02/2024,USD,1.23\n",
        encoding="utf8",
    )

    transactions = read_eri_raw(file_path)

    assert len(transactions) == 1
    entry = transactions[0]
    assert entry.isin == VALID_ISIN
    assert entry.date.isoformat() == "2024-02-01"
    assert entry.currency == "USD"
    assert entry.price == Decimal("1.23")


def test_read_eri_raw_raises_on_invalid_isin(tmp_path: Path) -> None:
    """Raise ParsingError when ISIN fails validation."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{INVALID_ISIN},01/02/2024,USD,1.23\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match=f"Invalid ISIN value '{INVALID_ISIN}'"):
        read_eri_raw(file_path)
