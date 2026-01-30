"""Tests for Schwab interest withholding tax handling."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import read_schwab_transactions
from cgt_calc.spin_off_handler import SpinOffHandler


def test_schwab_interest_tax_without_symbol_is_account_level() -> None:
    """Ensure Schwab NRA tax adjustments without symbols are treated as interest tax."""
    transactions = read_schwab_transactions(
        Path("tests/schwab/data/interest_tax/transactions.csv"),
        None,
    )
    assert any(t.action is ActionType.INTEREST for t in transactions)
    assert any(t.action is ActionType.INTEREST_TAX for t in transactions)

    date = datetime.date(2024, 6, 27)
    currency_converter = CurrencyConverter(None, {date: {"USD": Decimal(1)}})
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    calculator.convert_to_hmrc_transactions(transactions)
    report = calculator.calculate_capital_gain()

    assert report.total_foreign_interest == Decimal("2.94")
    assert report.total_foreign_interest_tax == Decimal("0.88")
