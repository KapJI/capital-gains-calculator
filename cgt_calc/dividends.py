"""Dividends."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime
from decimal import Decimal
import logging

from .model import Dividend, TaxTreaty
from .util import approx_equal

LOGGER = logging.getLogger(__name__)
DOUBLE_TAXATION_RULES = {
    "GBP": TaxTreaty("UK", Decimal(0), Decimal(0)),
    "USD": TaxTreaty("USA", Decimal(0.15), Decimal(0.15)),
    "PLN": TaxTreaty("Poland", Decimal(0.19), Decimal(0.1)),
}


def process_dividend(
    date: datetime.date,
    symbol: str,
    amount: Decimal,
    tax: Decimal | None,
    currency: str,
) -> Dividend:
    """Create dividend with matching tax treaty rule based on currency."""
    try:
        treaty = DOUBLE_TAXATION_RULES[currency]
    except KeyError:
        LOGGER.warning(
            "Taxation treaty for %s country is missing (ticker: %s), double "
            "taxation rules cannot be determined!",
            currency,
            symbol,
        )
        treaty = None
    else:
        assert treaty is not None
        expected_tax = treaty.country_rate * -amount
        if tax is not None and not approx_equal(expected_tax, tax):
            LOGGER.warning(
                "Determined double taxation treaty does not match the base "
                "taxation rules (expected %.2f base tax for %s but %.2f was deducted) "
                "for %s ticker!",
                expected_tax,
                treaty.country,
                tax,
                symbol,
            )
            treaty = None

    return Dividend(date, symbol, amount, tax or Decimal(0), treaty)
