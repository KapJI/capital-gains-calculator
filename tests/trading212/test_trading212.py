"""Tests for the Trading 212 parser."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.trading212 import (
    Trading212Column,
    Trading212Transaction,
    read_trading212_transactions,
)
from tests.utils import build_cmd

if TYPE_CHECKING:
    from collections.abc import Mapping


HEADER_2020 = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result (GBP)",
    "Total (GBP)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Charge amount (GBP)",
    "Transaction fee (GBP)",
    "Finra fee",
    "Currency (Finra fee)",
    "Notes",
    "ID",
]


HEADER_2024 = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result",
    "Currency (Result)",
    "Total",
    "Currency (Total)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Transaction fee",
    "Notes",
    "ID",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "Currency (Transaction fee)",
]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")


def _make_row(
    header: list[str], overrides: Mapping[str | Trading212Column, str]
) -> list[str]:
    row = dict.fromkeys(header, "")
    for column, value in overrides.items():
        column_name = column.value if isinstance(column, Trading212Column) else column
        row[column_name] = value
    return [row[column_name] for column_name in header]


def _prepare_file(tmp_path: Path, rows: list[list[str]]) -> Path:
    folder = tmp_path / "inputs"
    folder.mkdir()
    csv_file = folder / "trading212.csv"
    _write_csv(csv_file, rows)
    return folder


def test_read_trading212_transactions_supports_2020_export(tmp_path: Path) -> None:
    """Parse transactions using the legacy 2020 column set."""

    rows = [
        HEADER_2020,
        _make_row(
            HEADER_2020,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2020-06-24 04:06:06",
                Trading212Column.TOTAL_GBP: "5000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-1",
            },
        ),
        _make_row(
            HEADER_2020,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2020-06-24 14:33:50",
                Trading212Column.ISIN: "US0000000001",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL_GBP: "16.60",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-1",
            },
        ),
        _make_row(
            HEADER_2020,
            {
                Trading212Column.ACTION: "Dividend (Ordinary)",
                Trading212Column.TIME: "2021-04-02 10:15:07",
                Trading212Column.ISIN: "US0000000002",
                Trading212Column.TICKER: "BAR",
                Trading212Column.NAME: "Bar Inc",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "0.20",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "Not available",
                Trading212Column.TOTAL_GBP: "0.40",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "dividend-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = read_trading212_transactions(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.DIVIDEND,
    ]

    deposit = transactions[0]
    assert deposit.amount == Decimal("5000.00")
    assert deposit.currency == "GBP"

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.symbol == "FOO"
    assert buy.quantity == Decimal(2)
    assert buy.amount == Decimal("-16.60")
    assert buy.currency == "GBP"
    assert buy.price == Decimal("8.30")
    assert buy.price_foreign == Decimal("10.50")
    assert buy.exchange_rate == Decimal("1.25")

    dividend = transactions[2]
    assert isinstance(dividend, Trading212Transaction)
    assert dividend.amount == Decimal("0.40")
    assert dividend.currency == "GBP"


def test_read_trading212_transactions_supports_2024_export(tmp_path: Path) -> None:
    """Parse transactions using the modern 2024 column set."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2024-01-01 00:15:20.149",
                Trading212Column.TOTAL: "3000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-2",
            },
        ),
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 16:10:05.175",
                Trading212Column.ISIN: "US0000000003",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "16.60",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_FEE: "0.20",
                Trading212Column.CURRENCY_TRANSACTION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-2",
            },
        ),
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Dividend (Dividend)",
                Trading212Column.TIME: "2024-03-23 18:35:26",
                Trading212Column.ISIN: "US0000000003",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "0.05",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "Not available",
                Trading212Column.TOTAL: "0.40",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.WITHHOLDING_TAX: "0.05",
                Trading212Column.CURRENCY_WITHHOLDING_TAX: "USD",
                Trading212Column.TRANSACTION_ID: "dividend-2",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = read_trading212_transactions(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.DIVIDEND,
    ]

    deposit = transactions[0]
    assert deposit.amount == Decimal("3000.00")
    assert deposit.currency == "GBP"

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.amount == Decimal("-16.60")
    assert buy.transaction_fee == Decimal("0.20")
    assert buy.currency == "GBP"
    assert buy.price_foreign == Decimal("10.50")
    assert buy.exchange_rate == Decimal("1.25")

    dividend = transactions[2]
    assert isinstance(dividend, Trading212Transaction)
    assert dividend.amount == Decimal("0.40")
    assert dividend.currency == "GBP"
    assert dividend.transaction_fee == Decimal(0)


def test_read_trading212_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when a decimal value is invalid."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 10:00:00",
                Trading212Column.ISIN: "US0000000001",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "invalid",
                Trading212Column.CURRENCY_TOTAL: "GBP",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    with pytest.raises(ParsingError) as exc:
        read_trading212_transactions(folder)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in Total" in message


def test_run_with_trading212_2024_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/2024/inputs/",
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
        Path("tests") / "trading212" / "data" / "2024" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
