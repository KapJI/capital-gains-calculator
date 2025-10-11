"""Test Schwab."""

from pathlib import Path
import subprocess

from tests.utils import build_cmd


def test_run_with_schwab_example_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab",
        "tests/schwab/data/2023/transactions.csv",
    )
    result = subprocess.run(cmd, check=True, capture_output=True)
    stderr_lines = result.stderr.decode().strip().split("\n")
    assert len(stderr_lines) == 1
    assert stderr_lines[0] == "WARNING: No Schwab Award file provided"
    expected_file = Path("tests") / "schwab" / "data" / "2023" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout.decode("utf-8") == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
