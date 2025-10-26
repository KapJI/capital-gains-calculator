"""Test Schwab parser."""

from pathlib import Path
import subprocess

import pytest

from tests.utils import build_cmd


def test_run_with_schwab_example_2023_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/2023/transactions.csv",
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
    assert stderr_lines[0] == "WARNING: No Schwab Award file provided"
    expected_file = Path("tests") / "schwab" / "data" / "2023" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
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
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    stderr_lines = result.stderr.strip().split("\n")
    expected_lines = 2
    assert len(stderr_lines) == expected_lines
    assert stderr_lines[0] == "WARNING: No Schwab Award file provided"
    assert stderr_lines[1].startswith("WARNING: Cash Merger support is not complete")
    expected_file = (
        Path("tests") / "schwab" / "data" / "cash_merger" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
