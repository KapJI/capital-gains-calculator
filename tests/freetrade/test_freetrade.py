"""Test Freetrade support."""

import csv
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import (
    ParsingError,
    UnsupportedBrokerActionError,
    UnsupportedBrokerCurrencyError,
)
from cgt_calc.model import ActionType
from cgt_calc.parsers.freetrade import (
    COLUMNS,
    FreetradeColumn,
    FreetradeTransaction,
    read_freetrade_transactions,
)
from tests.utils import build_cmd

BASE_ROW_VALUES = {
    FreetradeColumn.TITLE.value: "Buy Apple",
    FreetradeColumn.TYPE.value: "ORDER",
    FreetradeColumn.TIMESTAMP.value: "2024-01-01T10:00:00",
    FreetradeColumn.ACCOUNT_CURRENCY.value: "GBP",
    FreetradeColumn.TOTAL_AMOUNT.value: "100",
    FreetradeColumn.BUY_SELL.value: "BUY",
    FreetradeColumn.TICKER.value: "AAPL",
    FreetradeColumn.ISIN.value: "US0378331005",
    FreetradeColumn.PRICE_PER_SHARE_ACCOUNT.value: "100",
    FreetradeColumn.STAMP_DUTY.value: "0",
    FreetradeColumn.QUANTITY.value: "1",
    FreetradeColumn.VENUE.value: "",
    FreetradeColumn.ORDER_ID.value: "123",
    FreetradeColumn.ORDER_TYPE.value: "MARKET",
    FreetradeColumn.INSTRUMENT_CURRENCY.value: "GBP",
    FreetradeColumn.TOTAL_SHARES_AMOUNT.value: "100",
    FreetradeColumn.PRICE_PER_SHARE.value: "100",
    FreetradeColumn.FX_RATE.value: "1",
    FreetradeColumn.BASE_FX_RATE.value: "1",
    FreetradeColumn.FX_FEE_BPS.value: "0",
    FreetradeColumn.FX_FEE_AMOUNT.value: "0",
    FreetradeColumn.DIVIDEND_EX_DATE.value: "",
    FreetradeColumn.DIVIDEND_PAY_DATE.value: "",
    FreetradeColumn.DIVIDEND_ELIGIBLE_QUANTITY.value: "",
    FreetradeColumn.DIVIDEND_AMOUNT_PER_SHARE.value: "0",
    FreetradeColumn.DIVIDEND_GROSS_AMOUNT.value: "0",
    FreetradeColumn.DIVIDEND_NET_AMOUNT.value: "0",
    FreetradeColumn.DIVIDEND_WITHHELD_PERCENTAGE.value: "0",
    FreetradeColumn.DIVIDEND_WITHHELD_AMOUNT.value: "0",
}


def _default_row(overrides: dict[str, str] | None = None) -> list[str]:
    """Return default row data with optional overrides."""
    values = BASE_ROW_VALUES.copy()
    if overrides:
        values.update(overrides)
    return [values[column] for column in COLUMNS]


def _write_csv(
    tmp_path: Path, header: list[str], rows: list[list[str]] | None = None
) -> Path:
    """Write CSV file with provided header and rows."""
    target = tmp_path / "freetrade.csv"
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        if rows:
            writer.writerows(rows)
    return target


def test_run_with_freetrade_file() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--freetrade-file",
        "tests/freetrade/data/transactions.csv",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert result.stderr == "", "Run with example files generated errors"
    expected_file = Path("tests") / "freetrade" / "data" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param if param else "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_read_freetrade_transactions_empty_file(tmp_path: Path) -> None:
    """Ensure parser raises when CSV is empty."""
    empty_file = tmp_path / "empty.csv"
    empty_file.write_text("")

    with pytest.raises(ParsingError):
        read_freetrade_transactions(empty_file)


def test_read_freetrade_transactions_missing_column(tmp_path: Path) -> None:
    """Missing required columns trigger ParsingError."""
    header = COLUMNS[:-1]
    path = _write_csv(tmp_path, header)

    with pytest.raises(ParsingError, match="Missing columns"):
        read_freetrade_transactions(path)


def test_read_freetrade_transactions_unknown_column(tmp_path: Path) -> None:
    """Unknown columns trigger ParsingError."""
    header = [*COLUMNS, "Unexpected"]
    path = _write_csv(tmp_path, header)

    with pytest.raises(ParsingError, match="Unknown columns: Unexpected"):
        read_freetrade_transactions(path)


def test_read_freetrade_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Invalid decimal values surface as ParsingError with row context."""
    overrides = {FreetradeColumn.QUANTITY.value: "not-a-number"}
    path = _write_csv(tmp_path, COLUMNS, [_default_row(overrides)])

    with pytest.raises(
        ParsingError,
        match=", row 2: Invalid decimal in column 'Quantity'",
    ):
        read_freetrade_transactions(path)


def test_read_freetrade_transactions_unsupported_currency(tmp_path: Path) -> None:
    """Non-GBP account currencies raise a dedicated error."""
    overrides = {FreetradeColumn.ACCOUNT_CURRENCY.value: "USD"}
    path = _write_csv(tmp_path, COLUMNS, [_default_row(overrides)])

    with pytest.raises(
        UnsupportedBrokerCurrencyError,
        match="parser does not support the provided account currency",
    ):
        read_freetrade_transactions(path)


def test_read_freetrade_transactions_success(tmp_path: Path) -> None:
    """Default row parses into a valid BUY transaction."""
    path = _write_csv(tmp_path, COLUMNS, [_default_row()])

    transactions = read_freetrade_transactions(path)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "AAPL"
    assert transaction.quantity == Decimal(1)
    assert transaction.price == Decimal(100)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_freetrade_transaction_unsupported_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsupported action types raise a helpful error."""

    def fake_action_from_str(action_type: str, buy_sell: str, file: Path) -> ActionType:
        return ActionType.ADJUSTMENT

    monkeypatch.setattr(
        "cgt_calc.parsers.freetrade.action_from_str", fake_action_from_str
    )
    dummy_file = tmp_path / "dummy.csv"
    dummy_file.write_text("")
    row = _default_row({FreetradeColumn.TYPE.value: "ADJUSTMENT"})

    with pytest.raises(
        UnsupportedBrokerActionError,
        match="Unsupported Freetrade action 'ADJUSTMENT'",
    ):
        FreetradeTransaction(COLUMNS, row, dummy_file)
