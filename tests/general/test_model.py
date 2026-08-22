"""Tests for the value types in cgt_calc.model."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.model import ActionType, BrokerTransaction, Isin

# Apple, a real ISIN with a valid check digit.
VALID_ISIN = "US0378331005"


def test_isin_accepts_a_valid_identifier() -> None:
    """A well-formed ISIN is preserved verbatim."""
    assert Isin(VALID_ISIN) == VALID_ISIN


def test_isin_normalises_case_and_whitespace() -> None:
    """Lowercase input and stray whitespace are normalised away."""
    assert Isin("  us0378331005 ") == VALID_ISIN


@pytest.mark.parametrize(
    "value",
    [
        "",
        "US037833100",  # too short
        "US03783310055",  # too long
        "US037833100X",  # check digit is not a digit
        "0S0378331005",  # country code is not alphabetic
    ],
)
def test_isin_rejects_malformed_identifiers(value: str) -> None:
    """Anything that is not ISIN-shaped is refused."""
    with pytest.raises(ValueError, match="Invalid ISIN"):
        Isin(value)


def test_isin_rejects_a_bad_check_digit() -> None:
    """A correctly shaped identifier with a wrong checksum is refused."""
    with pytest.raises(ValueError, match="Invalid ISIN checksum"):
        Isin("US0378331006")


def test_isin_is_usable_as_a_string() -> None:
    """The type stays interchangeable with str for lookups and formatting."""
    isin = Isin(VALID_ISIN)
    assert isinstance(isin, str)
    assert {VALID_ISIN: "AAPL"}[isin] == "AAPL"
    assert f"{isin}" == VALID_ISIN


def test_isin_rejects_attribute_assignment() -> None:
    """The value type carries no instance dict."""
    isin = Isin(VALID_ISIN)
    with pytest.raises(AttributeError):
        isin.symbol = "AAPL"  # type: ignore[attr-defined]


def test_isin_parse_returns_none_for_invalid_input() -> None:
    """The lenient constructor reports failure instead of raising."""
    assert Isin.parse("not-an-isin") is None


def test_isin_parse_normalises_valid_input() -> None:
    """The lenient constructor normalises exactly like the strict one."""
    assert Isin.parse("us0378331005") == Isin(VALID_ISIN)


def _transaction(isin: Isin | None) -> BrokerTransaction:
    return BrokerTransaction(
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
        isin=isin,
    )


def test_broker_transaction_rejects_a_bare_invalid_isin() -> None:
    """A caller that skips the type still cannot smuggle in a bad ISIN."""
    with pytest.raises(ValueError, match="Invalid ISIN"):
        _transaction("NOTANISIN")  # type: ignore[arg-type]


def test_broker_transaction_coerces_a_bare_valid_isin() -> None:
    """A valid bare string is normalised into the value type."""
    transaction = _transaction("us0378331005")  # type: ignore[arg-type]
    assert isinstance(transaction.isin, Isin)
    assert transaction.isin == VALID_ISIN


def test_broker_transaction_keeps_an_isin_instance_as_is() -> None:
    """An already-validated identifier passes through untouched."""
    isin = Isin(VALID_ISIN)
    assert _transaction(isin).isin is isin
