"""Unit and integration tests for transaction dumping functionality."""

from __future__ import annotations

import datetime
from decimal import Decimal
import io
import subprocess
from typing import TYPE_CHECKING

from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
from cgt_calc.transaction_dumper import dump_transactions
import dump_transactions as dump_script
from tests.utils import build_cmd, report_path

if TYPE_CHECKING:
    import pytest


def _sample_transactions() -> list[BrokerTransaction]:
    """Generate sample transactions for testing."""
    return [
        BrokerTransaction(
            date=datetime.date(2023, 4, 25),
            action=ActionType.STOCK_ACTIVITY,
            symbol="GOOG",
            description="RS",
            quantity=Decimal("10.5"),
            price=Decimal("100.25"),
            fees=Decimal("1.50"),
            amount=Decimal("1054.125"),
            currency=CurrencyCode("USD"),
            broker="Test Broker",
        ),
        BrokerTransaction(
            date=datetime.date(2023, 5, 1),
            action=ActionType.DIVIDEND,
            symbol=None,
            description="Dividend",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("50.00"),
            currency=CurrencyCode("GBP"),
            broker="",
        ),
        BrokerTransaction(
            date=datetime.date(2023, 6, 1),
            action=ActionType.BUY,
            symbol="AAPL",
            description="Buy AAPL",
            quantity=Decimal(5),
            price=Decimal(0),
            fees=Decimal(0),
            amount=Decimal(0),
            currency=CurrencyCode("USD"),
            broker="Broker B",
        ),
    ]


def test_dump_transactions_empty(capsys: pytest.CaptureFixture[str]) -> None:
    """An empty list prints an informative message to stderr."""
    buf = io.StringIO()
    dump_transactions([], stream=buf)

    assert buf.getvalue() == ""
    captured = capsys.readouterr()
    assert "No transactions loaded." in captured.err


def test_dump_transactions_formatting() -> None:
    """Transactions are formatted in a clean table with headers and totals."""
    buf = io.StringIO()
    transactions = _sample_transactions()
    dump_transactions(transactions, stream=buf)

    output = buf.getvalue()
    lines = [line for line in output.splitlines() if line]

    assert len(lines) == 7
    assert "Date" in lines[0]
    assert "Action" in lines[0]
    assert "Symbol" in lines[0]
    assert "Qty" in lines[0]
    assert "Price" in lines[0]
    assert "Amount" in lines[0]
    assert "Fees" in lines[0]
    assert "Broker" in lines[0]

    # Row 1: all fields present
    assert (
        lines[2]
        == "2023-04-25 | STOCK_ACTIVITY         | GOOG   |         10.5 |       100.25 |      1054.12 USD |     1.50 | Test Broker    "
    )

    # Row 2: None fields display as "-"
    assert (
        lines[3]
        == "2023-05-01 | DIVIDEND               | -      |            - |            - |        50.00 GBP |     0.00 | -              "
    )

    # Row 3: price of Decimal(0) displays as "0"
    assert (
        lines[4]
        == "2023-06-01 | BUY                    | AAPL   |            5 |            0 |         0.00 USD |     0.00 | Broker B       "
    )

    assert lines[6] == "Total: 3 transaction(s)"


def test_cli_dump_transactions_only_exits_without_calculations(
    request: pytest.FixtureRequest,
) -> None:
    """--dump-transactions only dumps the table and exits 0 without calculating."""
    cmd = build_cmd(
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v1.json",
        "--dump-transactions",
        "only",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "Date       | Action                 | Symbol" in result.stdout
    assert "STOCK_ACTIVITY         | GOOG" in result.stdout
    assert "Total: 4 transaction(s)" in result.stdout
    # Ensure calculations and summary were not performed
    assert "Tax summary" not in result.stdout
    assert "Capital gains" not in result.stdout


def test_cli_dump_transactions_exit_mode(
    request: pytest.FixtureRequest,
) -> None:
    """--dump-transactions exit mode behaves identically to only mode."""
    cmd = build_cmd(
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v1.json",
        "--dump-transactions",
        "exit",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "Total: 4 transaction(s)" in result.stdout
    assert "Tax summary" not in result.stdout


def test_cli_dump_transactions_continue_runs_calculations(
    request: pytest.FixtureRequest,
) -> None:
    """--dump-transactions continue mode dumps table and continues calculations."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "continue",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    # Both dump table and calculation summary should be in stdout
    assert "Date       | Action                 | Symbol" in result.stdout
    assert "Total: 8 transaction(s)" in result.stdout
    assert "Tax summary for 2023/2024" in result.stdout
    assert "Capital gains" in result.stdout


def test_cli_dump_transactions_flag_alone_defaults_to_continue(
    request: pytest.FixtureRequest,
) -> None:
    """--dump-transactions without value defaults to continue mode."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "Total: 8 transaction(s)" in result.stdout
    assert "Tax summary for 2023/2024" in result.stdout


def test_dump_transactions_script_entry_point() -> None:
    """dump_transactions.py wrapper runs correctly and returns 0."""
    exit_code = dump_script.main(
        [
            "--schwab-equity-award-json",
            "tests/schwab/data/equity_award/schwab_equity_award_v1.json",
        ]
    )
    assert exit_code == 0
