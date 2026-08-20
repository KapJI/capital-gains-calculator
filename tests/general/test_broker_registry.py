"""Tests for the broker registry."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cgt_calc.args_parser import create_parser
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.parsers.broker_registry import BrokerRegistry

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_load_all_transactions_without_files(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warn when no transactions are found."""
    args = create_parser().parse_args(["--year", "2023"])

    with caplog.at_level(logging.WARNING):
        transactions = BrokerRegistry.load_all_transactions(args, IsinConverter())

    assert transactions == []
    assert "Found 0 broker transactions" in caplog.text


def test_load_all_transactions_from_raw_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Load and sort transactions from a single broker."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD\n"
        "2022-11-14,SELL,META,19,116.00,0.05,USD\n"
    )
    args = create_parser().parse_args(["--year", "2023", "--raw-file", str(raw_file)])

    with caplog.at_level(logging.INFO):
        transactions = BrokerRegistry.load_all_transactions(args, IsinConverter())

    assert len(transactions) == 2
    assert "Loaded 2 transactions from RAW format" in caplog.text
    # Transactions are sorted by date.
    assert [str(transaction.date) for transaction in transactions] == [
        "2022-11-14",
        "2023-02-09",
    ]
