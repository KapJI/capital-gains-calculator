"""Tests for Morgan Stanley parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.mssb import read_mssb_transactions

if TYPE_CHECKING:
    from pathlib import Path


def test_read_mssb_transactions_empty_file(tmp_path: Path) -> None:
    """Ensure parser fails fast when the CSV file has no content."""
    empty_file = tmp_path / "Withdrawals Report.csv"
    empty_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        read_mssb_transactions(tmp_path)

    assert "CSV file is empty" in str(exc.value)
