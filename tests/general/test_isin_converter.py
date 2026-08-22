"""Tests for IsinConverter network behaviour."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from pathlib import Path
from typing import NoReturn

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.const import RuntimeMode
from cgt_calc.exceptions import (
    ExternalApiError,
    InvalidTransactionError,
    IsinTranslationError,
    ParsingError,
    UnexpectedColumnCountError,
)
import cgt_calc.isin_converter
from cgt_calc.isin_converter import IsinConverter, IsinTranslationEntry
from cgt_calc.model import ActionType, BrokerTransaction, Isin

# Well-formed but unassigned, so it cannot be in the bundled translation data.
UNKNOWN_ISIN = Isin("ZZ0000000008")


class OfflineSession:
    """Session stub that fails every request."""

    def post(
        self,
        url: str,
        json: list[dict[str, str]],
        headers: dict[str, str],
        timeout: int,
    ) -> NoReturn:
        """Simulate a network failure."""
        raise requests_exceptions.ConnectionError(f"offline: {url}")


def test_live_isin_lookup_announces_itself(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live OpenFIGI lookup logs a progress line before the request."""
    converter = IsinConverter()
    monkeypatch.setattr(converter, "session", OfflineSession())

    with caplog.at_level(logging.INFO), pytest.raises(ExternalApiError):
        converter.get_symbols(UNKNOWN_ISIN)

    assert f"Looking up ISIN {UNKNOWN_ISIN} via OpenFIGI..." in caplog.text


# Real, valid ISINs that are not in the bundled translation data.
ISIN_A = Isin("US0378331005")
ISIN_B = Isin("US5949181045")

FigiData = list[dict[str, str]]


class FakeResponse:
    """Canned OpenFIGI response."""

    def __init__(self, payload: list[dict[str, FigiData]]) -> None:
        """Store the payload."""
        self._payload = payload
        self.text = str(payload)

    def json(self) -> list[dict[str, FigiData]]:
        """Return the payload."""
        return self._payload


class FakeSession:
    """Session stub returning a canned response."""

    def __init__(self, payload: list[dict[str, FigiData]]) -> None:
        """Store the payload."""
        self._payload = payload
        self.calls = 0

    def post(
        self,
        url: str,
        json: list[dict[str, str]],
        headers: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        """Return the canned response."""
        self.calls += 1
        return FakeResponse(self._payload)


def _transaction(isin: Isin, symbol: str | None) -> BrokerTransaction:
    """Create a minimal transaction with the given ISIN and symbol."""
    return BrokerTransaction(
        date=datetime.date(2023, 1, 1),
        action=ActionType.BUY,
        symbol=symbol,
        description="test",
        quantity=Decimal(1),
        price=Decimal(1),
        fees=Decimal(0),
        amount=Decimal(-1),
        currency="USD",
        broker="Test",
        isin=isin,
    )


def _converter_with_session(
    monkeypatch: pytest.MonkeyPatch, payload: list[dict[str, FigiData]]
) -> tuple[IsinConverter, FakeSession]:
    """Create a converter with a canned OpenFIGI session."""
    converter = IsinConverter()
    session = FakeSession(payload)
    monkeypatch.setattr(converter, "session", session)
    return converter, session


def test_fetch_live_prefers_london_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer LN tickers and cache the result."""
    converter, session = _converter_with_session(
        monkeypatch,
        [
            {
                "data": [
                    {"ticker": "FOO", "exchCode": "LN"},
                    {"ticker": "FOOL", "exchCode": "LI"},
                    {"ticker": "FOOUS", "exchCode": "UN"},
                ]
            }
        ],
    )

    assert converter.get_symbols(ISIN_A) == {"FOO"}
    # The second lookup is served from the cache.
    assert converter.get_symbols(ISIN_A) == {"FOO"}
    assert session.calls == 1


def test_fetch_live_falls_back_to_uk_exchanges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fall back to other UK exchanges when LN is missing."""
    converter, _ = _converter_with_session(
        monkeypatch,
        [
            {
                "data": [
                    {"ticker": "FOOL", "exchCode": "LI"},
                    {"ticker": "FOOUS", "exchCode": "UN"},
                ]
            }
        ],
    )

    assert converter.get_symbols(ISIN_A) == {"FOOL"}


def test_fetch_live_falls_back_to_shortest_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the shortest ticker when no UK exchange is available."""
    converter, _ = _converter_with_session(
        monkeypatch,
        [
            {
                "data": [
                    {"ticker": "FOOLONG", "exchCode": "UN"},
                    {"ticker": "FOO", "exchCode": "UW"},
                ]
            }
        ],
    )

    assert converter.get_symbols(ISIN_A) == {"FOO"}


def test_fetch_live_invalid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return no symbols on unexpected responses."""
    converter, _ = _converter_with_session(monkeypatch, [{}])

    assert converter.get_symbols(ISIN_A) == set()


def test_fetch_live_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return no symbols when the response has no tickers."""
    converter, _ = _converter_with_session(monkeypatch, [{"data": []}])

    assert converter.get_symbols(ISIN_A) == set()


def test_validate_data_rejects_empty_symbol() -> None:
    """Reject ticker lists containing empty values."""
    converter = IsinConverter()
    converter.data = {ISIN_A: {"FOO", ""}}

    with pytest.raises(IsinTranslationError, match="contains an empty value"):
        converter.validate_data()


def test_validate_data_rejects_conflicting_symbols() -> None:
    """Reject the same ticker linked to two ISINs."""
    converter = IsinConverter()
    converter.data = {ISIN_A: {"FOO"}, ISIN_B: {"FOO"}}

    with pytest.raises(IsinTranslationError, match="already linked"):
        converter.validate_data()


def test_add_from_transaction_rejects_mismatched_symbol() -> None:
    """Reject transactions conflicting with the existing mapping."""
    converter = IsinConverter()
    converter.data[ISIN_A] = {"FOO"}

    with pytest.raises(InvalidTransactionError, match="does not match"):
        converter.add_from_transaction(_transaction(ISIN_A, "BAR"))


def test_add_from_transaction_adds_mapping() -> None:
    """Add new mappings and expose them in the symbol map."""
    converter = IsinConverter()

    converter.add_from_transaction(_transaction(ISIN_A, "FOO"))

    assert converter.get_symbols(ISIN_A) == {"FOO"}
    assert converter.get_symbol_to_isin_map()["FOO"] == ISIN_A


def test_translation_file_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write learned mappings to the translation file and read them back."""
    monkeypatch.setattr(cgt_calc.isin_converter, "CGT_MODE", RuntimeMode.PROD)
    translation_file = tmp_path / "isin_translation.csv"

    converter = IsinConverter(isin_translation_file=translation_file)
    converter.add_from_transaction(_transaction(ISIN_A, "FOO"))

    assert translation_file.read_text() == f"ISIN,symbol\n{ISIN_A},FOO\n"

    restored = IsinConverter(isin_translation_file=translation_file)
    assert restored.get_symbols(ISIN_A) == {"FOO"}


def test_translation_file_empty(tmp_path: Path) -> None:
    """Treat an empty translation file as no data."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text("")

    converter = IsinConverter(isin_translation_file=translation_file)

    assert converter.write_data == {}


def test_translation_file_bad_header(tmp_path: Path) -> None:
    """Raise on translation files with an unexpected header."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text("Wrong,Header\n")

    with pytest.raises(ParsingError, match="Unexpected header"):
        IsinConverter(isin_translation_file=translation_file)


def test_translation_file_invalid_row(tmp_path: Path) -> None:
    """Raise with row context on invalid translation rows."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text("ISIN,symbol\nNOTANISIN,FOO\n")

    with pytest.raises(ParsingError, match="invalid ISIN") as excinfo:
        IsinConverter(isin_translation_file=translation_file)

    assert excinfo.value.row_index == 2


def test_translation_entry() -> None:
    """Translation entries validate their rows."""
    entry = IsinTranslationEntry([ISIN_A, "FOO"], Path("test.csv"))
    assert str(entry) == f"ISIN: {ISIN_A}, symbol: {{'FOO'}}"

    with pytest.raises(UnexpectedColumnCountError):
        IsinTranslationEntry([ISIN_A], Path("test.csv"))
