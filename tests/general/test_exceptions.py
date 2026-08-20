"""Unit tests for custom exception helpers."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.exceptions import (
    AmountMissingError,
    CalculatedAmountDiscrepancyError,
    ExchangeRateMissingError,
    LatexRenderError,
    ParsingError,
    PriceMissingError,
    QuantityMissingError,
    QuantityNotPositiveError,
    SymbolMissingError,
    UnexpectedRowCountError,
    UnsupportedBrokerActionError,
    UnsupportedBrokerCurrencyError,
)
from cgt_calc.model import ActionType, BrokerTransaction

TRANSACTION = BrokerTransaction(
    date=datetime.date(2023, 1, 1),
    action=ActionType.BUY,
    symbol="FOO",
    description="test",
    quantity=Decimal(1),
    price=Decimal(1),
    fees=Decimal(0),
    amount=Decimal(-1),
    currency="USD",
    broker="Test",
)


def test_parsing_error_message_without_row() -> None:
    """Base message includes file and original detail."""

    err = ParsingError(Path("file.csv"), "broken header")

    assert str(err) == "While parsing file.csv: broken header"


def test_parsing_error_message_with_row() -> None:
    """Row context is included when supplied at construction."""

    err = ParsingError(Path("file.csv"), "bad value", row_index=7)

    assert str(err) == "While parsing file.csv, row 7: bad value"


def test_parsing_error_add_row_context_updates_message() -> None:
    """Adding row context after creation updates the message."""

    err = ParsingError(Path("file.csv"), "bad value")

    err.add_row_context(9)
    assert str(err) == "While parsing file.csv, row 9: bad value"

    err.add_row_context(11)
    assert str(err) == "While parsing file.csv, row 11: bad value"


def test_transaction_error_messages() -> None:
    """Transaction errors describe what is missing."""

    assert "Amount missing" in str(AmountMissingError(TRANSACTION))
    assert "Symbol missing" in str(SymbolMissingError(TRANSACTION))
    assert "Price missing" in str(PriceMissingError(TRANSACTION))
    assert "Quantity missing" in str(QuantityMissingError(TRANSACTION))
    assert "Positive quantity" in str(QuantityNotPositiveError(TRANSACTION))
    assert "-42" in str(CalculatedAmountDiscrepancyError(TRANSACTION, Decimal(-42)))


def test_parsing_error_subclasses() -> None:
    """Parsing errors embed the offending file and detail."""

    assert "SELL" in str(UnsupportedBrokerActionError(Path("f.csv"), "Broker", "SELL"))
    assert "XXX" in str(UnsupportedBrokerCurrencyError(Path("f.csv"), "Broker", "XXX"))
    assert "6 rows" in str(UnexpectedRowCountError(6, Path("f.csv")))


def test_other_error_messages() -> None:
    """Calculation and rendering errors describe their context."""

    assert "USD" in str(ExchangeRateMissingError("USD", datetime.date(2023, 1, 1)))
    assert "render.log" in str(LatexRenderError(Path("render.log")))
