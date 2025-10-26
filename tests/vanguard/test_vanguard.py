"""Test Vanguard parser support."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.vanguard import COLUMNS, read_vanguard_transactions
from tests.utils import build_cmd


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")


def test_run_with_vanguard_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2022",
        "--vanguard-file",
        "tests/vanguard/data/report.csv",
        "--interest-fund-tickers",
        "FOO",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert result.stderr == "", "Run with example files generated errors"
    expected_file = Path("tests") / "vanguard" / "data" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_read_vanguard_transactions_buy(tmp_path: Path) -> None:
    """Parse a simple BUY transaction and compute derived fields."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund (FOO)",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = read_vanguard_transactions(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "FOO"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal(10)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when amount cannot be parsed as Decimal."""

    vanguard_file = tmp_path / "invalid.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund (FOO)",
            "not-a-number",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    with pytest.raises(ParsingError) as exc:
        read_vanguard_transactions(vanguard_file)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal" in message


def test_read_vanguard_transactions_invalid_header(tmp_path: Path) -> None:
    """Raise ParsingError when header contains unexpected column."""

    vanguard_file = tmp_path / "invalid_header.csv"
    rows = [
        [
            "Date",
            "Details",
            "Unexpected",
            "Balance",
        ],
    ]
    _write_csv(vanguard_file, rows)

    with pytest.raises(ParsingError) as exc:
        read_vanguard_transactions(vanguard_file)

    assert "Expected column 3 to be 'Amount' but found 'Unexpected'" in str(exc.value)


def test_read_vanguard_transactions_empty_file(tmp_path: Path) -> None:
    """Raise ParsingError when file has no content."""

    vanguard_file = tmp_path / "empty.csv"
    vanguard_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        read_vanguard_transactions(vanguard_file)

    assert "Vanguard CSV file is empty" in str(exc.value)
