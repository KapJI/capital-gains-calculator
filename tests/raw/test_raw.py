"""Test raw format support."""

from __future__ import annotations

import csv
from decimal import Decimal
import logging
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.raw import (
    COLUMNS,
    RawColumn,
    _parse_decimal,
    read_raw_transactions,
)
from tests.utils import build_cmd


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write CSV rows to disk."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_run_with_raw_files_no_balance_check() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2022",
        "--raw-file",
        "tests/raw/data/test_data.csv",
        "--no-balance-check",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    stderr_lines = result.stderr.strip().split("\n")
    assert len(stderr_lines) == 1
    assert stderr_lines[0].startswith("WARNING: Bed and breakfasting for META"), (
        "Unexpected stderr message"
    )
    expected_file = Path("tests") / "raw" / "data" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_read_raw_transactions_with_header(tmp_path: Path) -> None:
    """Parse a RAW file including a header row."""

    raw_file = tmp_path / "raw_with_header.csv"
    rows = [
        COLUMNS,
        [
            "2024-01-02",
            "BUY",
            "XYZ",
            "10",
            "2.50",
            "0.10",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    transactions = read_raw_transactions(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.BUY
    assert transaction.symbol == "XYZ"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal("2.50")
    assert transaction.amount == Decimal("-25.10")
    assert transaction.fees == Decimal("0.10")


def test_read_raw_transactions_without_header(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Parse RAW rows without a header."""

    raw_file = tmp_path / "raw_without_header.csv"
    rows = [
        [
            "2024-01-03",
            "SELL",
            "XYZ",
            "5",
            "3.00",
            "0.00",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.raw"):
        transactions = read_raw_transactions(raw_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.SELL
    assert transaction.amount == Decimal("15.00")
    assert "missing header row" in caplog.text.lower()


def test_read_raw_transactions_invalid_header(tmp_path: Path) -> None:
    """Fail fast when header columns do not match expected schema."""

    raw_file = tmp_path / "raw_bad_header.csv"
    bad_header = ["date", "action", "ticker", *COLUMNS[3:]]
    _write_csv(raw_file, [bad_header])

    with pytest.raises(ParsingError) as exc:
        read_raw_transactions(raw_file)

    assert "Expected column 3" in str(exc.value)


def test_read_raw_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when decimal conversion fails."""

    raw_file = tmp_path / "raw_bad_decimal.csv"
    rows = [
        COLUMNS,
        [
            "2024-01-04",
            "DIVIDEND",
            "XYZ",
            "bad",
            "0.10",
            "",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    with pytest.raises(ParsingError) as exc:
        read_raw_transactions(raw_file)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in column 'quantity'" in message


def test_read_raw_transactions_empty_file(tmp_path: Path) -> None:
    """Error when RAW CSV is empty."""

    raw_file = tmp_path / "empty.csv"
    raw_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        read_raw_transactions(raw_file)

    assert "CSV file is empty" in str(exc.value)


def test_read_raw_transactions_applies_ticker_renames(tmp_path: Path) -> None:
    """Rename known tickers according to configuration."""

    raw_file = tmp_path / "raw_ticker_rename.csv"
    rows = [
        COLUMNS,
        [
            "2024-01-05",
            "BUY",
            "FB",
            "1",
            "10.00",
            "0.00",
            "USD",
        ],
    ]
    _write_csv(raw_file, rows)

    transactions = read_raw_transactions(raw_file)

    assert len(transactions) == 1
    assert transactions[0].symbol == "META"


def test_parse_decimal_missing_value_raises() -> None:
    """Ensure empty required decimals raise an explicit error."""

    row = dict.fromkeys(RawColumn, "")

    with pytest.raises(ValueError, match="Missing value in column 'quantity'"):
        _parse_decimal(row, RawColumn.QUANTITY, allow_empty=False)
