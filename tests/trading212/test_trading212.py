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
    Trading212Parser,
    Trading212Transaction,
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

HEADER_2026 = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "Notes",
    "ID",
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
    "Stamp duty reserve tax",
    "Currency (Stamp duty reserve tax)",
    "Transaction fee",
    "Finra fee",
    "Currency conversion from amount",
    "Currency (Currency conversion from amount)",
    "Currency conversion to amount",
    "Currency (Currency conversion to amount)",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "Currency (Transaction fee)",
    "Currency (Finra fee)",
    "Merchant name",
    "Merchant category",
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

    transactions = Trading212Parser().load_from_dir(folder)

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

    transactions = Trading212Parser().load_from_dir(folder)

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


def test_read_trading212_transactions_supports_2026_export(tmp_path: Path) -> None:
    """Parse transactions using the newer 2026 column set."""

    rows = [
        HEADER_2026,
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2025-04-07 09:15:20.149",
                Trading212Column.TOTAL: "1000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-3",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-08 10:10:05.175",
                Trading212Column.ISIN: "US0000000004",
                Trading212Column.TICKER: "QUX",
                Trading212Column.NAME: "Qux Ltd",
                Trading212Column.NO_OF_SHARES: "4",
                Trading212Column.PRICE_PER_SHARE: "12.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "41.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.STAMP_DUTY_RESERVE_TAX: "0.50",
                Trading212Column.CURRENCY_STAMP_DUTY_RESERVE_TAX: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT: "50.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT: "USD",
                Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT: "41.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FEE: "1.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-3",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market sell",
                Trading212Column.TIME: "2025-04-08 15:05:00",
                Trading212Column.ISIN: "US0000000004",
                Trading212Column.TICKER: "QUX",
                Trading212Column.NAME: "Qux Ltd",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "16.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "sell-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Card debit",
                Trading212Column.TIME: "2025-04-09 12:00:00",
                Trading212Column.TOTAL: "20.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.NOTES: "Latte",
                Trading212Column.MERCHANT_NAME: "Cafe 212",
                Trading212Column.MERCHANT_CATEGORY: "Food & Drink",
                Trading212Column.TRANSACTION_ID: "debit-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Currency conversion",
                Trading212Column.TIME: "2025-04-09 14:00:00",
                Trading212Column.TOTAL: "-0.10",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT: "10.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT: "USD",
                Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT: "8.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FEE: "0.10",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "conversion-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Card refund",
                Trading212Column.TIME: "2025-04-10 09:30:00",
                Trading212Column.TOTAL: "5.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.NOTES: "Latte refund",
                Trading212Column.MERCHANT_NAME: "Cafe 212",
                Trading212Column.MERCHANT_CATEGORY: "Food & Drink",
                Trading212Column.TRANSACTION_ID: "refund-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Spending cashback",
                Trading212Column.TIME: "2025-04-11 08:00:00",
                Trading212Column.TOTAL: "0.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.NOTES: "Cashback",
                Trading212Column.TRANSACTION_ID: "cashback-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.SELL,
        ActionType.TRANSFER,
        ActionType.ADJUSTMENT,
        ActionType.TRANSFER,
        ActionType.ADJUSTMENT,
    ]

    deposit = transactions[0]
    assert deposit.action == ActionType.TRANSFER
    assert deposit.amount == Decimal("1000.00")
    assert deposit.currency == "GBP"

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.action == ActionType.BUY
    assert buy.amount == Decimal("-41.00")
    assert buy.currency == "GBP"
    assert buy.conversion_fee == Decimal("1.00")
    assert buy.price_foreign == Decimal("12.50")
    assert buy.exchange_rate == Decimal("1.25")
    assert buy.stamp_duty == Decimal("0.50")

    sell = transactions[2]
    assert isinstance(sell, Trading212Transaction)
    assert sell.action == ActionType.SELL
    assert sell.amount == Decimal("16.00")
    assert sell.currency == "GBP"
    assert sell.price_foreign == Decimal("10.00")
    assert sell.exchange_rate == Decimal("1.25")

    debit = transactions[3]
    assert isinstance(debit, Trading212Transaction)
    assert debit.action == ActionType.TRANSFER
    assert debit.amount == Decimal("20.00")
    assert debit.currency == "GBP"
    assert debit.notes == "Latte"

    conversion = transactions[4]
    assert isinstance(conversion, Trading212Transaction)
    assert conversion.action == ActionType.ADJUSTMENT
    assert conversion.amount == Decimal("-0.10")
    assert conversion.currency == "GBP"
    assert conversion.conversion_fee == Decimal("0.10")

    refund = transactions[5]
    assert isinstance(refund, Trading212Transaction)
    assert refund.action == ActionType.TRANSFER
    assert refund.amount == Decimal("5.00")
    assert refund.currency == "GBP"
    assert refund.notes == "Latte refund"

    cashback = transactions[6]
    assert isinstance(cashback, Trading212Transaction)
    assert cashback.action == ActionType.ADJUSTMENT
    assert cashback.amount == Decimal("0.50")
    assert cashback.currency == "GBP"
    assert cashback.notes == "Cashback"


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
        Trading212Parser().load_from_dir(folder)

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
