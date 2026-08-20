"""Tests for initial stock prices."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.exceptions import ExchangeRateMissingError, ParsingError
from cgt_calc.initial_prices import InitialPrices, InitialPricesEntry


def test_load_custom_file(tmp_path: Path) -> None:
    """Load prices from a custom file."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,10.5\n')

    prices = InitialPrices(initial_prices_file=prices_file)

    assert prices.get(datetime.date(2021, 3, 8), "FOO") == Decimal("10.5")


def test_get_missing_price(tmp_path: Path) -> None:
    """Raise when no price is stored for the date and symbol."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text("date,symbol,price\n")

    prices = InitialPrices(initial_prices_file=prices_file)

    with pytest.raises(ExchangeRateMissingError):
        prices.get(datetime.date(2021, 3, 8), "FOO")


def test_invalid_row(tmp_path: Path) -> None:
    """Raise with row context on invalid rows."""
    prices_file = tmp_path / "initial_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO\n')

    with pytest.raises(ParsingError) as excinfo:
        InitialPrices(initial_prices_file=prices_file)

    assert excinfo.value.row_index == 2


def test_entry_str() -> None:
    """Entries have a readable representation."""
    entry = InitialPricesEntry(
        ["Mar 08, 2021", "FOO", "10.5"], Path("initial_prices.csv")
    )

    assert str(entry) == "date: 2021-03-08, symbol: FOO, price: 10.5"
