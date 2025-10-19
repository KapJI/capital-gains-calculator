"""Tests for currency converter exchange rate loading."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.exceptions import ParsingError

if TYPE_CHECKING:
    from pathlib import Path


def test_read_exchange_rates_successfully(tmp_path: Path) -> None:
    """Loading a populated rates file captures every row."""
    rates_file = tmp_path / "rates.csv"
    rates_file.write_text(
        "month,currency,rate\n2024-01-01,USD,1.25\n2024-02-01,EUR,1.10\n",
        encoding="utf8",
    )

    converter = CurrencyConverter(exchange_rates_file=rates_file)

    january_rates = converter.cache[datetime.date(2024, 1, 1)]
    february_rates = converter.cache[datetime.date(2024, 2, 1)]
    assert january_rates == {"USD": Decimal("1.25")}
    assert february_rates == {"EUR": Decimal("1.10")}


def test_read_exchange_rates_raises_on_empty_file(tmp_path: Path) -> None:
    """Empty rates files raise a parsing error."""
    rates_file = tmp_path / "empty.csv"
    # create an empty file
    rates_file.touch()

    with pytest.raises(ParsingError, match="Exchange rate file is empty"):
        CurrencyConverter(exchange_rates_file=rates_file)
