"""Test Schwab parser."""

import datetime
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser
from tests.utils import build_cmd


def test_missing_award_file_reports_the_vest_it_cannot_price() -> None:
    """A vest without a price is the only thing the award file is needed for.

    The runs below parse files that never need it and stay silent; this one
    reaches a Stock Plan Activity row and has nothing to price it with.
    """
    path = Path("tests") / "schwab" / "data" / "rsu_settlement" / "transactions.csv"
    SchwabParser.awards_prices = AwardPrices(award_prices={})

    with (
        path.open(encoding="utf-8") as csv_file,
        pytest.raises(ParsingError) as exc_info,
    ):
        SchwabParser.read_transactions(csv_file, path)

    message = str(exc_info.value)
    assert "no Schwab Award file was provided" in message
    assert "--schwab-award-file" in message
    # The row that needs the file is named, so it can be found in the statement.
    assert "BAR" in message
    assert "2023-08-18" in message


def test_award_file_without_the_award_says_so() -> None:
    """A file that lacks this particular award is a different mistake."""
    path = Path("tests") / "schwab" / "data" / "rsu_settlement" / "transactions.csv"
    SchwabParser.awards_prices = AwardPrices(
        award_prices={datetime.date(2020, 1, 1): {"FOO": Decimal(1)}}
    )

    with (
        path.open(encoding="utf-8") as csv_file,
        pytest.raises(ParsingError) as exc_info,
    ):
        SchwabParser.read_transactions(csv_file, path)

    assert "has no award price for it" in str(exc_info.value)


def test_run_with_schwab_example_2023_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/2023/transactions.csv",
        "--output",
        "out/test-schwab-2023/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert result.stderr.strip() == "", "Unexpected stderr message"
    expected_file = Path("tests") / "schwab" / "data" / "2023" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_cash_merger_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--schwab-file",
        "tests/schwab/data/cash_merger/transactions.csv",
        "--output",
        "out/test-schwab-cash-merger/",
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
    assert stderr_lines[0].startswith("WARNING: Cash Merger support is not complete")
    expected_file = (
        Path("tests") / "schwab" / "data" / "cash_merger" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_rsu_settlement_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/rsu_settlement/transactions.csv",
        "--schwab-award-file",
        "tests/schwab/data/rsu_settlement/awards.csv",
        "--output",
        "out/test-schwab-rsu_settlement/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    stderr = result.stderr.strip()
    assert stderr == ""
    expected_file = (
        Path("tests") / "schwab" / "data" / "rsu_settlement" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_bond_interest_files() -> None:
    """Test that Bond Interest and Credit Interest are classified as INTEREST."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/bond_interest/transactions.csv",
        "--output",
        "out/test-schwab-bond_interest/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert result.stderr.strip() == "", "Unexpected stderr message"
    expected_file = (
        Path("tests") / "schwab" / "data" / "bond_interest" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
