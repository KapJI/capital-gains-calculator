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
    MSSBParser,
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
        MSSBParser().load_from_dir(tmp_path)

    assert "CSV doesn't have a header" in str(exc.value)


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

    transactions = MSSBParser().load_from_dir(tmp_path)

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

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal("-4218.95")
    assert transaction.fees == Decimal(0)


def test_read_mssb_withdrawal_with_uppercase_csv_extension(tmp_path: Path) -> None:
    """Parse a withdrawals report whose CSV extension is uppercase."""
    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME.replace(".csv", ".CSV")
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "02-Apr-2021",
            "ORDER-UPPERCASE",
            "Cash",
            "Sale",
            "Complete",
            "$1.00",
            "-100.00",
            "$100.00",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser.load_from_dir(tmp_path)

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.TRANSFER
    assert transactions[0].amount == Decimal("-100.00")


def test_read_mssb_withdrawal_goog_sale_before_split_window(tmp_path: Path) -> None:
    """Ensure a GOOG sale shortly before the July 15, 2022 split is adjusted.

    Morgan Stanley's own notice states that sales occurring "on or prior to
    the July 15, 2022 stock split are reflected in pre-split" values. This
    covers the previously mishandled window (2022-06-15 to 2022-07-14) where
    a bug in STOCK_SPLIT_INFO used June 15 instead of July 15, causing these
    sales to be skipped for the 20x split adjustment.
    """

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "01-Jul-2022",
            "ORDER-4",
            "GSU Class C",
            "Sale",
            "Complete",
            "$2,240.00",
            "-2.00",
            "$4,479.90",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.SELL
    expected_symbol = TICKER_RENAMES.get("GOOG", "GOOG")
    assert transaction.symbol == expected_symbol
    # Pre-split values reported by Morgan Stanley get multiplied by the
    # split factor (20) for quantity and divided by it for price.
    assert transaction.quantity == Decimal("40.00")
    assert transaction.price == Decimal("112.00")


def test_read_mssb_withdrawal_goog_sale_on_split_date_is_adjusted(
    tmp_path: Path,
) -> None:
    """Adjust a GOOG sale on July 15 because the report uses pre-split values."""

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "15-Jul-2022",
            "ORDER-5",
            "GSU Class C",
            "Sale",
            "Complete",
            "$2,240.00",
            "-2.00",
            "$4,479.90",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    # The report notice says activity "on or prior to" July 15 is pre-split.
    assert transaction.quantity == Decimal("40.00")
    assert transaction.price == Decimal("112.00")


def test_read_mssb_withdrawal_goog_sale_after_split_not_adjusted(
    tmp_path: Path,
) -> None:
    """Leave a GOOG sale after July 15 in its exported post-split values."""

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        [
            "18-Jul-2022",
            "ORDER-6",
            "GSU Class C",
            "Sale",
            "Complete",
            "$112.00",
            "-40.00",
            "$4,479.90",
            "0",
            "N/A",
        ],
    ]
    _write_csv(withdrawal_file, rows)

    transactions = MSSBParser().load_from_dir(tmp_path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.quantity == Decimal("40.00")
    assert transaction.price == Decimal("112.00")


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
        MSSBParser().load_from_dir(tmp_path)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in column 'Quantity'" in message


def test_read_mssb_release_invalid_header(tmp_path: Path) -> None:
    """Error when release report header differs from expected schema."""

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    invalid_header = ["Vest date", *COLUMNS_RELEASE[1:]]
    _write_csv(release_file, [invalid_header])

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    assert "CSV header mismatch" in str(exc.value)


def test_read_mssb_release_header_only_file(tmp_path: Path) -> None:
    """Raise a clean 'file is empty' error for a header with zero data rows.

    Regression test: `csv.DictReader` has no `__bool__`/`__len__`, so
    `bool(reader)` is always truthy even when the file has no data rows,
    which used to make the "file is empty" check dead code.
    """

    release_file = tmp_path / RELEASES_REPORT_FILENAME
    _write_csv(release_file, [COLUMNS_RELEASE])

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    assert "file is empty" in str(exc.value)


def test_read_mssb_withdrawal_short_row(tmp_path: Path) -> None:
    """Raise a clean ParsingError for a row with fewer fields than the header.

    Regression test: a row with fewer fields than the header gets padded by
    `csv.DictReader` with `None` values for the missing trailing columns
    (rather than being rejected outright). Before the fix, this crashed deep
    in `_parse_decimal` with an unhandled `AttributeError` ('NoneType' object
    has no attribute 'replace') instead of raising a clean ParsingError with
    row/file context.
    """

    withdrawal_file = tmp_path / WITHDRAWALS_REPORT_FILENAME
    rows = [
        COLUMNS_WITHDRAWAL,
        # Only the first 6 columns are provided; Quantity, Net Amount,
        # Net Share Proceeds and Tax Payment Method are missing entirely.
        ["02-Apr-2021", "ORDER-2", "Cash", "Sale", "Complete", "$10.00"],
    ]
    _write_csv(withdrawal_file, rows)

    with pytest.raises(ParsingError) as exc:
        MSSBParser().load_from_dir(tmp_path)

    # It must be a *clean* ParsingError with row/file context, not an
    # unhandled AttributeError/TypeError bubbling out of the parser.
    assert exc.type is ParsingError
    assert "row 2" in str(exc.value)
