"""Tests for the Sharesight parser."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.sharesight import parse_income_report, parse_trade_report

if TYPE_CHECKING:
    from pathlib import Path
from pathlib import Path
import subprocess

from tests.utils import build_cmd


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_run_with_sharesight_files_no_balance_check() -> None:
    """Runs the tool and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--sharesight-dir",
        "tests/sharesight/data/inputs/",
        "--no-balance-check",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert result.stderr == "", "Run with example files generated errors"
    expected_file = (
        Path("tests")
        / "sharesight"
        / "data"
        / "test_run_with_sharesight_files_no_balance_check_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_parse_income_report_missing_local_column(tmp_path: Path) -> None:
    """Error when Sharesight local dividend header omits required column."""
    file_path = tmp_path / "Taxable Income Report.csv"
    _write_csv(
        file_path,
        [
            ["Test Portfolio"],
            [""],
            ["Local Income"],
            [""],
            ["Dividend Payments"],
            [
                "Code",
                "Name",
                "Date Paid",
                "Net Dividend",
                "Tax Deducted",
                "Tax Credit",
                "Comments",
            ],
            ["ABC", "Example", "01/01/2020", "10", "0", "0", "Note"],
            ["Total"],
        ],
    )

    with pytest.raises(
        ParsingError,
        match="Missing expected columns in Sharesight local dividend header: Gross Dividend",
    ) as excinfo:
        list(parse_income_report(file_path))

    assert excinfo.value.row_index == 6


def test_parse_income_report_missing_foreign_column(tmp_path: Path) -> None:
    """Error when Sharesight foreign dividend header omits required column."""
    file_path = tmp_path / "Taxable Income Report.csv"
    _write_csv(
        file_path,
        [
            ["Test Portfolio"],
            [""],
            ["Foreign Income"],
            [
                "Code",
                "Name",
                "Date Paid",
                "Exchange Rate",
                "Currency",
                "Net Amount",
                "Gross Amount",
                "Comments",
            ],
            ["ABC", "Example", "01/02/2020", "1.23", "USD", "10", "12", "Note"],
            ["Total"],
        ],
    )

    with pytest.raises(
        ParsingError,
        match="Missing expected columns in Sharesight foreign dividend header: Foreign Tax Deducted",
    ) as excinfo:
        list(parse_income_report(file_path))

    assert excinfo.value.row_index == 4


def test_parse_trade_report_missing_column(tmp_path: Path) -> None:
    """Error when trades header omits a required column."""
    file_path = tmp_path / "All Trades Report.csv"
    _write_csv(
        file_path,
        [
            [
                "Market",
                "Code",
                "Name",
                "Type",
                "Date",
                "Quantity",
                "Price *",
                "Brokerage *",
                "Currency",
                "Exchange Rate",
                "Comments",
            ],
            [
                "NASDAQ",
                "ABC",
                "Example",
                "Buy",
                "01/01/2020",
                "1",
                "100",
                "0",
                "USD",
                "1.2",
                "Note",
            ],
        ],
    )

    with pytest.raises(
        ParsingError,
        match="Missing expected columns in Sharesight trades header: Value",
    ) as excinfo:
        list(parse_trade_report(file_path))

    assert excinfo.value.row_index == 1


def test_parse_trade_report_invalid_decimal(tmp_path: Path) -> None:
    """Expose row index and column name when decimal parsing fails."""
    file_path = tmp_path / "All Trades Report.csv"
    _write_csv(
        file_path,
        [
            [
                "Market",
                "Code",
                "Name",
                "Type",
                "Date",
                "Quantity",
                "Price *",
                "Brokerage *",
                "Currency",
                "Exchange Rate",
                "Value",
                "",
                "Comments",
            ],
            [
                "NASDAQ",
                "ABC",
                "Example",
                "Buy",
                "01/01/2020",
                "oops",
                "100",
                "0",
                "USD",
                "1.2",
                "1000",
                "",
                "Note",
            ],
        ],
    )

    with pytest.raises(ParsingError, match=r"Invalid decimal.*Quantity") as excinfo:
        list(parse_trade_report(file_path))

    assert excinfo.value.row_index == 2
