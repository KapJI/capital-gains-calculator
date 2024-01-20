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


def test_schwab_transaction_v1() -> None:
    """Test read_schwab_equity_award_json_transactions() on v1 data."""
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions(
        "tests/test_data/schwab_equity_award_v1.json"
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


def test_schwab_transaction_v2() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2 data."""
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions(
        "tests/test_data/schwab_equity_award_v2.json"
    )

    assert transactions[0].date == datetime.date(2023, 4, 25)
    assert transactions[0].action == ActionType.STOCK_ACTIVITY
    assert transactions[0].quantity == Decimal("4.911")
    assert transactions[0].price == Decimal("106.78")
    assert transactions[0].fees == Decimal("0")

    assert transactions[1].date == datetime.date(2023, 4, 25)
    assert transactions[1].action == ActionType.STOCK_ACTIVITY
    assert transactions[1].quantity == Decimal("13.6")
    assert transactions[1].price == Decimal("106.78")
    assert transactions[1].fees == Decimal("0")

    assert transactions[2].date == datetime.date(2023, 8, 29)
    assert transactions[2].action == ActionType.SELL
    assert transactions[2].quantity == Decimal("14.40")
    assert transactions[2].price == Decimal("137.90")
    assert transactions[2].fees == Decimal("0.02")

    assert transactions[3].date == datetime.date(2023, 9, 25)
    assert transactions[3].action == ActionType.STOCK_ACTIVITY
    assert transactions[3].symbol == "GOOG"
    assert transactions[3].quantity == Decimal("4.911")
    assert transactions[3].price == Decimal("131.25")
    assert transactions[3].fees == Decimal("0")
    assert transactions[3].currency == "USD"
    assert transactions[3].broker == "Charles Schwab"

    assert transactions[4].date == datetime.date(2023, 9, 25)
    assert transactions[4].action == ActionType.STOCK_ACTIVITY
    assert transactions[4].symbol == "GOOG"
    assert transactions[4].quantity == Decimal("13.6")
    assert transactions[4].price == Decimal("131.25")
    assert transactions[4].fees == Decimal("0")
    assert transactions[4].currency == "USD"
    assert transactions[4].broker == "Charles Schwab"
