"""Tests for Morgan Stanley parser."""

from __future__ import annotations

import csv
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.mssb import (
    COLUMNS_RELEASE,
    COLUMNS_WITHDRAWAL,
    RELEASES_REPORT_FILENAME,
    WITHDRAWALS_REPORT_FILENAME,
    read_mssb_transactions,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_csv(file_path: Path, rows: list[list[str]]) -> None:
    """Write rows to a CSV file."""

    with file_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_read_mssb_transactions_empty_file(tmp_path: Path) -> None:
    """Ensure parser fails fast when the CSV file has no content."""
    empty_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    empty_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        read_mssb_transactions(tmp_path)

    assert "CSV file is empty" in str(exc.value)


def test_read_mssb_release_success(tmp_path: Path) -> None:
    """Parse a valid release report row and return a populated transaction."""

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    rows = [
        COLUMNS_RELEASE,
        [
            "25-Mar-2023",
            "ORDER-1",
            "GSU Class C",
            "Release",
            "Complete",
            "$10.00",
            "3.000",
            "$0.00",
            "3",
            "Fractional Shares",
        ],
    ]
    _write_csv(release_file, rows)

    transactions = read_mssb_transactions(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    expected_symbol = TICKER_RENAMES.get("GOOG", "GOOG")
    assert transaction.symbol == expected_symbol
    assert transaction.action == ActionType.STOCK_ACTIVITY
    assert transaction.quantity == Decimal(3)
    assert transaction.price == Decimal("10.00")
    assert transaction.amount == Decimal(30)


def test_read_mssb_withdrawal_skips_notice(tmp_path: Path) -> None:
    """Ensure withdrawal parser ignores trailing notice rows."""

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "02-Apr-2021",
            "ORDER-2",
            "Cash",
            "Sale",
            "Complete",
            "$1.00",
            "-4,218.95",
            "$4,218.95",
            "0",
            "N/A",
        ],
        [
            "Please note that any Alphabet share sales, transfers, or deposits that occurred on or prior to the July 15, 2022 stock split are reflected in pre-split. Any sales, transfers, or deposits that occurred after July 15, 2022 are in post-split values. For GSU vests, your activity is displayed in post-split values.",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = read_mssb_transactions(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal("-4218.95")
    assert transaction.fees == Decimal(0)


def test_read_mssb_withdrawal_invalid_decimal(tmp_path: Path) -> None:
    """Raise parsing error when numeric values cannot be parsed."""

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "09-Feb-2023",
            "ORDER-3",
            "GSU Class C",
            "Sale",
            "Complete",
            "$105.70",
            "bad",
            "$3,170.93",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    with pytest.raises(ParsingError) as exc:
        read_mssb_transactions(tmp_path)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in column 'Quantity'" in message


def test_read_mssb_release_invalid_header(tmp_path: Path) -> None:
    """Error when release report header differs from expected schema."""

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    invalid_header = ["Vest date", *COLUMNS_RELEASE[1:]]
    _write_csv(release_file, [invalid_header])

    with pytest.raises(ParsingError) as exc:
        read_mssb_transactions(tmp_path)

    assert "Expected column 1" in str(exc.value)
