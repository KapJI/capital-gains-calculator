"""Tests for the value types in cgt_calc.model."""

from __future__ import annotations

from dataclasses import replace
import datetime
from decimal import Decimal

import pytest

from cgt_calc.exceptions import CalculationError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    ForeignCurrencyAmount,
    Isin,
)

# Apple, a real ISIN with a valid check digit.
VALID_ISIN = "US0378331005"
USD = CurrencyCode("USD")


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


def _transaction(isin: Isin | None, currency: CurrencyCode = USD) -> BrokerTransaction:
    return BrokerTransaction(
        date=datetime.date(2023, 1, 1),
        action=ActionType.BUY,
        symbol="FOO",
        description="test",
        quantity=Decimal(1),
        price=Decimal(1),
        fees=Decimal(0),
        amount=Decimal(-1),
        currency=currency,
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


def test_currency_code_accepts_a_valid_code() -> None:
    """A well-formed ISO 4217 code is preserved verbatim."""
    assert CurrencyCode("USD") == "USD"


def test_currency_code_strips_whitespace() -> None:
    """Stray whitespace around the code is tolerated."""
    assert CurrencyCode("  USD ") == "USD"


@pytest.mark.parametrize("value", ["usd", "Usd", "GBp"])
def test_currency_code_does_not_fold_case(value: str) -> None:
    """Case is meaningful ("GBp" is pence), so it is never guessed at."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        CurrencyCode(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "US",  # too short
        "USDD",  # too long
        "US1",  # not alphabetic
        "£",  # a symbol, not a code
        "US Dollar",  # a name, not a code
        "GBp",  # pence, not a currency code
    ],
)
def test_currency_code_rejects_malformed_codes(value: str) -> None:
    """Anything that is not three letters is refused."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        CurrencyCode(value)


def test_currency_code_accepts_a_code_unknown_to_stale_tables() -> None:
    """Membership is HMRC's call via its rate table, not a bundled ISO list."""
    assert CurrencyCode("VES") == "VES"


def test_currency_code_is_usable_as_a_string() -> None:
    """The type stays interchangeable with str for lookups and formatting."""
    code = CurrencyCode("GBP")
    assert isinstance(code, str)
    assert {"GBP": 1}[code] == 1
    assert f"{code}" == "GBP"


def test_currency_code_rejects_attribute_assignment() -> None:
    """The value type carries no instance dict."""
    code = CurrencyCode("GBP")
    with pytest.raises(AttributeError):
        code.symbol = "£"  # type: ignore[attr-defined]


def test_currency_code_parse_returns_none_for_invalid_input() -> None:
    """The lenient constructor reports failure instead of raising."""
    assert CurrencyCode.parse("GBp") is None


def test_currency_code_parse_normalises_valid_input() -> None:
    """The lenient constructor normalises exactly like the strict one."""
    assert CurrencyCode.parse(" GBP ") == CurrencyCode("GBP")


def test_foreign_currency_amount_rejects_mixed_currencies() -> None:
    """Amounts sourced from incompatible input rows produce a domain error."""
    usd = ForeignCurrencyAmount(Decimal(1), CurrencyCode("USD"))
    gbp = ForeignCurrencyAmount(Decimal(1), CurrencyCode("GBP"))

    with pytest.raises(CalculationError, match="different currencies"):
        usd + gbp


def test_nonzero_foreign_amount_requires_a_currency() -> None:
    """A missing input currency produces a domain error rather than an assertion."""
    invalid = ForeignCurrencyAmount(Decimal(1))

    with pytest.raises(CalculationError, match="Currency missing"):
        ForeignCurrencyAmount() + invalid

    # The left operand is validated by the same rule as the right one.
    with pytest.raises(CalculationError, match="Currency missing"):
        invalid + ForeignCurrencyAmount()


def test_broker_transaction_rejects_a_bare_invalid_currency() -> None:
    """A caller that skips the type still cannot smuggle in a bad currency."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        _transaction(None, currency="GBp")  # type: ignore[arg-type]


def test_broker_transaction_coerces_a_bare_valid_currency() -> None:
    """A valid bare string is normalised into the value type."""
    transaction = _transaction(None, currency=" USD ")  # type: ignore[arg-type]
    assert isinstance(transaction.currency, CurrencyCode)
    assert transaction.currency == "USD"


def test_broker_transaction_keeps_a_currency_code_instance_as_is() -> None:
    """An already-validated code passes through untouched."""
    currency = CurrencyCode("USD")
    assert _transaction(None, currency=currency).currency is currency


def _transaction_with_fees(
    foreign_fees: dict[CurrencyCode, Decimal],
) -> BrokerTransaction:
    transaction = _transaction(None)
    return replace(transaction, foreign_fees=foreign_fees)


def test_broker_transaction_rejects_a_bare_invalid_foreign_fee_currency() -> None:
    """Foreign fee keys are held to the same rule as the transaction currency."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        _transaction_with_fees({"GBp": Decimal(1)})  # type: ignore[dict-item]


def test_broker_transaction_coerces_bare_valid_foreign_fee_currencies() -> None:
    """Valid bare keys are normalised into the value type."""
    transaction = _transaction_with_fees({" USD ": Decimal(1)})  # type: ignore[dict-item]
    assert list(transaction.foreign_fees) == [CurrencyCode("USD")]
    assert all(isinstance(key, CurrencyCode) for key in transaction.foreign_fees)


def test_broker_transaction_sums_foreign_fees_that_coerce_to_one_currency() -> None:
    """Keys that normalise to the same code add up rather than overwrite."""
    transaction = _transaction_with_fees(
        {" USD ": Decimal(1), "USD": Decimal(2)}  # type: ignore[dict-item]
    )
    assert transaction.foreign_fees == {CurrencyCode("USD"): Decimal(3)}


def test_broker_transaction_keeps_typed_foreign_fees_as_is() -> None:
    """An already-typed fee table passes through untouched."""
    fees = {CurrencyCode("USD"): Decimal(1)}
    assert _transaction_with_fees(fees).foreign_fees is fees
