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
    stderr_lines = result.stderr.decode().strip().split("\n")
    expected_lines = 2
    assert len(stderr_lines) == expected_lines
    assert stderr_lines[0] == "WARNING: No Schwab Award file provided"
    assert stderr_lines[1].startswith("WARNING: Cash Merger support is not complete")
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
