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
from tests.utils import build_cmd, stderr_alerts

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


HEADER_2025_BARE = [
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
    "Charge amount",
    "Currency (Charge amount)",
    "Deposit fee",
    "Currency (Deposit fee)",
    "Stamp duty",
    "Currency (Stamp duty)",
    "Stamp duty reserve tax",
    "Currency (Stamp duty reserve tax)",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "French transaction tax",
    "Currency (French transaction tax)",
]


def test_read_trading212_transactions_supports_2025_bare_columns(
    tmp_path: Path,
) -> None:
    """Parse exports with bare tax columns and 2025 action labels."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2025-04-01 08:00:00",
                Trading212Column.TOTAL: "1000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.DEPOSIT_FEE: "0.70",
                Trading212Column.CURRENCY_DEPOSIT_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-02 10:30:00",
                Trading212Column.ISIN: "GB0000000001",
                Trading212Column.TICKER: "WID",
                Trading212Column.NAME: "Widget plc",
                Trading212Column.NO_OF_SHARES: "50",
                Trading212Column.PRICE_PER_SHARE: "6.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: "301.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.STAMP_DUTY: "1.50",
                Trading212Column.CURRENCY_STAMP_DUTY: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-03 11:00:00",
                Trading212Column.ISIN: "FR0000000002",
                Trading212Column.TICKER: "FRA",
                Trading212Column.NAME: "Frenchie SA",
                Trading212Column.NO_OF_SHARES: "10",
                Trading212Column.PRICE_PER_SHARE: "20.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "EUR",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "161.36",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.FRENCH_TRANSACTION_TAX: "0.51",
                Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FEE: "0.85",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-2",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Card credit",
                Trading212Column.TIME: "2025-04-04 09:00:00",
                Trading212Column.TOTAL: "25.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "card-credit-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Dividend (Interest)",
                Trading212Column.TIME: "2025-04-05 12:00:00",
                Trading212Column.ISIN: "IE0000000003",
                Trading212Column.TICKER: "BOND",
                Trading212Column.NAME: "Bond Fund",
                Trading212Column.TOTAL: "0.08",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "interest-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Dividend (Property income distribution)",
                Trading212Column.TIME: "2025-04-06 12:00:00",
                Trading212Column.ISIN: "GB0000000004",
                Trading212Column.TICKER: "REIT",
                Trading212Column.NAME: "Property Trust",
                Trading212Column.TOTAL: "3.20",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "pid-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Dividend adjustment",
                Trading212Column.TIME: "2025-04-07 12:00:00",
                Trading212Column.ISIN: "GB0000000004",
                Trading212Column.TICKER: "REIT",
                Trading212Column.NAME: "Property Trust",
                Trading212Column.TOTAL: "-0.02",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "div-adj-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Spin off",
                Trading212Column.TIME: "2025-04-08 06:00:00",
                Trading212Column.ISIN: "US0000000005",
                Trading212Column.TICKER: "NEWCO",
                Trading212Column.NAME: "NewCo Inc",
                Trading212Column.NO_OF_SHARES: "5",
                Trading212Column.TOTAL: "0.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "spin-off-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.BUY,
        ActionType.TRANSFER,
        ActionType.INTEREST,
        ActionType.DIVIDEND,
        ActionType.DIVIDEND,
        ActionType.SPIN_OFF,
    ]

    deposit = transactions[0]
    assert deposit.action == ActionType.TRANSFER
    assert deposit.amount == Decimal("1000.00")

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.amount == Decimal("-301.50")
    assert buy.stamp_duty == Decimal("1.50")
    assert buy.fees == Decimal("1.50")
    assert buy.price == Decimal("6.00")

    buy_french = transactions[2]
    assert isinstance(buy_french, Trading212Transaction)
    assert buy_french.amount == Decimal("-161.36")
    assert buy_french.stamp_duty == Decimal("0.51")
    assert buy_french.conversion_fee == Decimal("0.85")
    assert buy_french.fees == Decimal("1.36")
    assert buy_french.price == Decimal("16.00")

    card_credit = transactions[3]
    assert card_credit.action == ActionType.TRANSFER
    assert card_credit.amount == Decimal("25.00")

    interest = transactions[4]
    assert interest.action == ActionType.INTEREST
    assert interest.amount == Decimal("0.08")
    assert interest.symbol == "BOND"

    pid = transactions[5]
    assert pid.action == ActionType.DIVIDEND
    assert pid.amount == Decimal("3.20")
    assert pid.symbol == "REIT"

    dividend_adjustment = transactions[6]
    assert dividend_adjustment.action == ActionType.DIVIDEND
    assert dividend_adjustment.amount == Decimal("-0.02")

    spin_off = transactions[7]
    assert spin_off.action == ActionType.SPIN_OFF
    assert spin_off.symbol == "NEWCO"
    assert spin_off.quantity == Decimal(5)


def test_read_trading212_transactions_non_gbp_french_tax(tmp_path: Path) -> None:
    """Store non-GBP French transaction tax in foreign_fees."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-03 11:00:00",
                Trading212Column.ISIN: "FR0000000002",
                Trading212Column.TICKER: "FRA",
                Trading212Column.NAME: "Frenchie SA",
                Trading212Column.NO_OF_SHARES: "10",
                Trading212Column.PRICE_PER_SHARE: "20.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "EUR",
                Trading212Column.TOTAL: "170.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.FRENCH_TRANSACTION_TAX: "0.60",
                Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX: "EUR",
                Trading212Column.TRANSACTION_ID: "buy-fr-tax",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.foreign_fees == {"EUR": Decimal("0.60")}
    assert buy.stamp_duty == Decimal(0)
    assert buy.fees == Decimal(0)
    assert buy.amount == Decimal("-170.00")


def test_read_trading212_transactions_non_gbp_stamp_duty(tmp_path: Path) -> None:
    """Store non-GBP stamp duty in foreign_fees."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-02 10:30:00",
                Trading212Column.ISIN: "GB0000000001",
                Trading212Column.TICKER: "WID",
                Trading212Column.NAME: "Widget plc",
                Trading212Column.NO_OF_SHARES: "50",
                Trading212Column.PRICE_PER_SHARE: "6.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: "301.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.STAMP_DUTY: "1.50",
                Trading212Column.CURRENCY_STAMP_DUTY: "EUR",
                Trading212Column.TRANSACTION_ID: "buy-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.foreign_fees == {"EUR": Decimal("1.50")}
    assert buy.stamp_duty == Decimal(0)
    assert buy.fees == Decimal(0)


def test_read_trading212_transactions_mixed_fee_currencies(tmp_path: Path) -> None:
    """Split fees between the account currency and foreign_fees."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 16:10:05",
                Trading212Column.ISIN: "US0000000003",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "17.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_FEE: "0.20",
                Trading212Column.CURRENCY_TRANSACTION_FEE: "USD",
                Trading212Column.CURRENCY_CONVERSION_FEE: "0.15",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-mixed",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.foreign_fees == {"USD": Decimal("0.20")}
    assert buy.transaction_fee == Decimal(0)
    assert buy.conversion_fee == Decimal("0.15")
    assert buy.fees == Decimal("0.15")


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
        "--output",
        "out/test-trading212/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests") / "trading212" / "data" / "2024" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
