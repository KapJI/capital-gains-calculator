"""Tests for Hargreaves Lansdown parser."""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from pathlib import Path
import shutil
import subprocess
import tempfile

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from tests.utils import build_cmd


def _create_buy_pdf(filename: str) -> None:
    """Generate a mock HL Buy Contract Note PDF."""
    c = canvas.Canvas(filename, pagesize=letter)
    _, height = letter

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 50, "Hargreaves Lansdown Contract Note")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, "Date 24/02/2026")
    c.drawString(50, height - 90, "Contract Note No. B302087054-02508682")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "**BOUGHT**")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 150, "ISIN: IE00BK5BQV03")
    c.drawString(50, height - 165, "STOCK CODE: VHVG")
    c.drawString(50, height - 180, "Security: Vanguard FTSE Developed World UCITS ETF")

    # Quantity | Price | Consideration
    c.drawString(50, height - 220, "4,563.00 | 3287.199 | 149,994.89")
    c.drawString(50, height - 260, "Dealing charge || 11.95")
    c.drawString(50, height - 290, "Settlement Date: 26/02/2026")

    c.save()


def _create_sell_pdf(filename: str) -> None:
    """Generate a mock HL Sell Contract Note PDF."""
    c = canvas.Canvas(filename, pagesize=letter)
    _, height = letter

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 50, "Hargreaves Lansdown Contract Note")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, "Date 25/02/2026")
    c.drawString(50, height - 90, "Contract Note No. S302087055-04500122")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "**SOLD**")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 150, "ISIN: IE00BK5BQV03")
    c.drawString(50, height - 165, "STOCK CODE: VHVG")
    c.drawString(50, height - 180, "Security: Vanguard FTSE Developed World UCITS ETF")

    # Quantity | Price | Consideration
    c.drawString(50, height - 220, "1,000.00 | 4500.00 | 45,000.00")
    c.drawString(50, height - 260, "Dealing charge || 11.95")
    c.drawString(50, height - 290, "Settlement Date: 27/02/2026")

    c.save()


def _prepare_pdf_test_data() -> str:
    test_dir = Path(__file__).resolve().parent

    # Set your source file path
    source_file = Path(test_dir / "data" / "inputs" / "hl-transaction-summary.csv")

    # Get system temp directory
    temp_dir = Path(tempfile.gettempdir())

    shutil.copy2(source_file, temp_dir)

    _create_buy_pdf(str(temp_dir / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(temp_dir / "S302087055_SOLD_Vanguard.pdf"))

    return str(temp_dir)


def test_hl_parser() -> None:
    """Runs the tool and verifies it doesn't fail."""

    test_output_dir = _prepare_pdf_test_data()
    cmd = build_cmd(
        "--year",
        "2025",
        "--hl-dir",
        test_output_dir,
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
    expected = expected.replace("tests/hl/data/inputs", test_output_dir)
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_hl_parser_missing_pdf() -> None:
    """Runs the tool and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2025",
        "--hl-dir",
        "tests/hl/data/inputs/",
        "--no-balance-check",
        "--output",
        "out/test-hl/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode, "Test succeeded but expected failure"
    assert "Cannot find contract note pdf" in result.stderr, (
        "Test failed with unexpected error"
    )
