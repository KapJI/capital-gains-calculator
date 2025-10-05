"""Test Trading212 support."""

from pathlib import Path
import subprocess

from .utils import build_cmd


def test_run_with_trading212_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212",
        "tests/test_data/trading212_2024/",
        "--isin-translation-file",
        "",
    )
    result = subprocess.run(cmd, check=True, capture_output=True)
    assert result.stderr == b"", "Run with example files generated errors"
    expected_file = (
        Path("tests") / "test_data" / "trading212_2024" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout.decode("utf-8") == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
