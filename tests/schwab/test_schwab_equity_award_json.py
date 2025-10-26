"""Unit tests on schwab_equity_award_json.py."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.model import ActionType
from cgt_calc.parsers import schwab_equity_award_json

# ruff: noqa: SLF001 "Private member accessed"


def test_decimal_from_str() -> None:
    """Test _decimal_from_str()."""
    assert schwab_equity_award_json._decimal_from_str("$123,456.23") == Decimal(
        "123456.23"
    )


def test_decimal_from_number_or_str_both() -> None:
    """Test _decimal_from_number_or_str() on float."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "123.45", "keySortValue": Decimal("67.89")}, "key"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_float_null() -> None:
    """Test _decimal_from_number_or_str() on None float."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "67.89", "keySortValue": None}, "key"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_float_custom_suffix() -> None:
    """Test _decimal_from_number_or_str_default_suffix() on float.

    With a custom suffix.
    """
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"keyMySuffix": Decimal("67.89")}, "key", "MySuffix"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_default() -> None:
    """Test _decimal_from_number_or_str() with absent keys."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "123.45", "keySortValue": 67.89}, "otherkey"
    ) == Decimal(0)


def test_schwab_transaction_v1() -> None:
    """Test read_schwab_equity_award_json_transactions() on v1 data."""
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions(
        Path("tests/schwab/data/equity_award/schwab_equity_award_v1.json")
    )

    assert transactions[0].date == datetime.date(2022, 4, 25)
    assert transactions[0].action == ActionType.STOCK_ACTIVITY
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal("67.2")
    assert transactions[0].price == Decimal("125.6445")
    assert transactions[0].fees == Decimal(0)
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    assert transactions[1].date == datetime.date(2022, 6, 14)
    assert transactions[1].action == ActionType.SELL
    assert transactions[1].quantity == Decimal("62.601495")
    assert transactions[1].price == Decimal("113.75")
    assert transactions[1].fees == Decimal("0.17")

    assert transactions[2].date == datetime.date(2022, 10, 25)
    assert transactions[2].action == ActionType.STOCK_ACTIVITY
    assert transactions[2].quantity == Decimal("10.45")
    assert transactions[2].price == Decimal("112.42")
    assert transactions[2].fees == Decimal(0)

    assert transactions[3].date == datetime.date(2022, 11, 16)
    assert transactions[3].action == ActionType.SELL
    assert transactions[3].quantity == Decimal("12.549")
    assert transactions[3].price is not None
    assert transactions[3].price.quantize(Decimal(".000001")).normalize() == Decimal(
        "2051.597737"
    )
    assert transactions[3].amount == Decimal(25745)
    assert transactions[3].fees == Decimal("0.50")


def test_schwab_transaction_v2() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2 data."""
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions(
        Path("tests/schwab/data/equity_award/schwab_equity_award_v2.json")
    )

    i = 0
    assert transactions[i].date == datetime.date(2023, 4, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].quantity == Decimal("4.911")
    assert transactions[i].price == Decimal("106.78")
    assert transactions[i].fees == Decimal(0)

    i += 1
    assert transactions[i].date == datetime.date(2023, 4, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].quantity == Decimal("13.6")
    assert transactions[i].price == Decimal("106.78")
    assert transactions[i].fees == Decimal(0)

    i += 1
    assert transactions[i].date == datetime.date(2023, 8, 31)
    assert transactions[i].action == ActionType.SELL
    assert transactions[i].quantity == Decimal("14.40")
    assert transactions[i].price == Decimal("137.90")
    assert transactions[i].fees == Decimal("0.02")

    i += 1
    assert transactions[i].date == datetime.date(2023, 9, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].quantity == Decimal("4.911")
    assert transactions[i].price == Decimal("131.25")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2023, 9, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].quantity == Decimal("13.6")
    assert transactions[i].price == Decimal("131.25")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 6, 17)
    assert transactions[i].action == ActionType.DIVIDEND
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("74.62")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 6, 17)
    assert transactions[i].action == ActionType.DIVIDEND_TAX
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("-22.39")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 7, 12)
    assert transactions[i].action == ActionType.DIVIDEND_TAX
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("11.20")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"


def test_schwab_transaction_v2_rounding() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2_rounding data.

    This tests 13 vesting events with 7 shares each, which are then sold.
    """
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions(
        Path("tests/schwab/data/equity_award/schwab_equity_award_v2_rounding.json")
    )

    assert transactions[0].date == datetime.date(2020, 4, 24)
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal(91)
    assert transactions[0].price == Decimal("102.4935")
    assert transactions[0].fees == Decimal("0.49")
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    num_vested = Decimal(0)
    for transaction_id in range(1, len(transactions)):
        assert transactions[transaction_id].date == (
            datetime.date(2020, 1, 1) + datetime.timedelta(days=transaction_id)
        )
        assert transactions[transaction_id].action == ActionType.STOCK_ACTIVITY
        assert transactions[transaction_id].quantity == Decimal(7)
        num_vested += transactions[transaction_id].quantity or Decimal(0)
        assert transactions[transaction_id].price == Decimal("123.45")
        assert transactions[transaction_id].fees == Decimal(0)
    assert num_vested == transactions[0].quantity
