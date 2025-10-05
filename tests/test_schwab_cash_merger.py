"""Test Schwab Cash Merger support."""

from pathlib import Path
import subprocess

from .utils import build_cmd


def test_run_with_schwab_cash_merger_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--schwab",
        "tests/test_data/schwab_cash_merger/transactions.csv",
    )
    result = subprocess.run(cmd, check=True, capture_output=True)
    assert result.stderr == b"", "Run with example files generated errors"
    expected_file = (
        Path("tests") / "test_data" / "schwab_cash_merger" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout.decode("utf-8") == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
