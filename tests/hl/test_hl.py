"""Tests for Hargreaves Lansdown parser."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from pathlib import Path
import subprocess

from tests.utils import build_cmd


def test_hl_parser() -> None:
    """Runs the tool and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--hl-dir",
        "tests/hl/data/inputs/",
        "--no-balance-check",
        "--output",
        "out/test-hl/",
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
        / "hl"
        / "data"
        / "test_run_with_hl_files_no_balance_check_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
