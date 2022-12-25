"""Unit tests on schwab_equity_award_json.py."""
from __future__ import annotations

import datetime
from decimal import Decimal

from cgt_calc.model import ActionType
from cgt_calc.parsers import schwab_equity_award_json


def test_get_decimal_or_default_present_int() -> None:
    """Test get_decimal_or_default() on an int."""
    assert schwab_equity_award_json._get_decimal_or_default(  # pylint: disable=W0212
        {"key": 1}, "key"
    ) == Decimal(1)


def test_get_decimal_or_default_present_float() -> None:
    """Test get_decimal_or_default() on a float."""
    assert schwab_equity_award_json._get_decimal_or_default(  # pylint: disable=W0212
        {"key": 1.0}, "key"
    ) == Decimal(1.0)


def test_get_decimal_or_default_absent() -> None:
    """Test get_decimal_or_default() on absent key."""
    assert schwab_equity_award_json._get_decimal_or_default(  # pylint: disable=W0212
        {"key": 1}, "otherkey", Decimal(0)
    ) == Decimal(0)


def test_price_from_str() -> None:
    """Test _price_from_str()."""
    assert schwab_equity_award_json._price_from_str(  # pylint: disable=W0212
        "$123,456.23"
    ) == Decimal("123456.23")


def test_price_from_str_or_float_str() -> None:
    """Test _price_from_str_or_float() on string."""
    assert schwab_equity_award_json._price_from_str_or_float(  # pylint: disable=W0212
        {"key": "123.45", "keySortValue": 67.89}, "key"
    ) == Decimal("123.45")


def test_price_from_str_or_float_str_null() -> None:
    """Test _price_from_str_or_float() on None string."""
    assert schwab_equity_award_json._price_from_str_or_float(  # pylint: disable=W0212
        {"key": None, "keySortValue": 67.89}, "key"
    ) == Decimal("67.89")


def test_price_from_str_or_float_float_default_suffix() -> None:
    """Test _price_from_str_or_float_default_suffix() on float.

    With the default suffix.
    """
    assert schwab_equity_award_json._price_from_str_or_float(  # pylint: disable=W0212
        {"keySortValue": 67.89}, "key"
    ) == Decimal("67.89")


def test_price_from_str_or_float_float_custom_suffix() -> None:
    """Test _price_from_str_or_float_default_suffix() on float.

    With a custom suffix.
    """
    assert schwab_equity_award_json._price_from_str_or_float(  # pylint: disable=W0212
        {"keyMySuffix": 67.89}, "key", "MySuffix"
    ) == Decimal("67.89")


def test_price_from_str_or_float_default() -> None:
    """Test _price_from_str_or_float() with absent keys."""
    assert schwab_equity_award_json._price_from_str_or_float(  # pylint: disable=W0212
        {"key": "123.45", "keySortValue": 67.89}, "otherkey"
    ) == Decimal("0")


def test_schwab_transaction() -> None:
    """Test read_schwab_equity_award_json_transactions()."""
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions(
        "tests/test_data/schwab_equity_award.json"
    )

    assert transactions[0].date == datetime.date(2022, 4, 25)
    assert transactions[0].action == ActionType.STOCK_ACTIVITY
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal("67.2")
    assert transactions[0].price == Decimal("125.6445")
    assert transactions[0].fees == Decimal("0")
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    assert transactions[1].date == datetime.date(2022, 6, 10)
    assert transactions[1].action == ActionType.SELL
    assert transactions[1].quantity == Decimal("62.6015")
    assert transactions[1].price == Decimal("113.75")
    assert transactions[1].fees == Decimal("0.17")

    assert transactions[2].date == datetime.date(2022, 10, 25)
    assert transactions[2].action == ActionType.STOCK_ACTIVITY
    assert transactions[2].quantity == Decimal("10.45")
    assert transactions[2].price == Decimal("112.42")
    assert transactions[2].fees == Decimal("0")

    assert transactions[3].date == datetime.date(2022, 11, 14)
    assert transactions[3].action == ActionType.SELL
    assert transactions[3].quantity == Decimal("12.549")
    assert transactions[3].price.quantize(  # type: ignore
        Decimal(".000001")
    ).normalize() == Decimal("2051.597737")
    assert transactions[3].amount == Decimal("25745")
    assert transactions[3].fees == Decimal("0.50")
