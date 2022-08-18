"""Unit tests on schwab_equity_award_json.py."""
from __future__ import annotations

import json
import datetime
from decimal import Decimal
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay
from pathlib import Path
from typing import Any, Dict, List

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

from cgt_calc.parsers import schwab_equity_award_json

def test_get_decimal_present_int():
    assert schwab_equity_award_json._get_decimal({'key' : 1}, 'key') == Decimal(1)

def test_get_decimal_present_float():
    assert schwab_equity_award_json._get_decimal({'key' : 1.0}, 'key') == Decimal(1.0)

def test_get_decimal_absent():
    assert schwab_equity_award_json._get_decimal({'key' : 1}, 'otherkey', Decimal(0)) == Decimal(0)

def test_price_from_str():
    assert schwab_equity_award_json._price_from_str("$123,456.23") == Decimal('123456.23')

def test_SchwabTransaction():
    transactions = schwab_equity_award_json.read_schwab_equity_award_json_transactions("tests/test_data/schwab_equity_award.json")

    assert transactions[0].date == datetime.date(2022, 4, 25)
    assert transactions[0].action == ActionType.STOCK_ACTIVITY
    assert transactions[0].symbol == 'GOOG'
    assert transactions[0].quantity == Decimal('67.2')
    assert transactions[0].price == Decimal('125.64')
    assert transactions[0].fees == Decimal('0')
    assert transactions[0].currency == 'USD'
    assert transactions[0].broker == 'Charles Schwab'

    assert transactions[1].date == datetime.date(2022, 6, 17)
    assert transactions[1].action == ActionType.SELL
    assert transactions[1].quantity == Decimal('62.6')
    assert transactions[1].price == Decimal('113.75')
    assert transactions[1].fees == Decimal('0.17')

    assert transactions[2].date == datetime.date(2022, 10, 25)
    assert transactions[2].action == ActionType.STOCK_ACTIVITY
    assert transactions[2].quantity == Decimal('10.45')
    assert transactions[2].price == Decimal('112.42')
    assert transactions[2].fees == Decimal('0')
