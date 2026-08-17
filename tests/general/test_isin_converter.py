"""Tests for IsinConverter network behaviour."""

from __future__ import annotations

import logging
from typing import NoReturn

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.exceptions import ExternalApiError
from cgt_calc.isin_converter import IsinConverter

# Deliberately not a real ISIN so it can't be in the bundled translation data.
UNKNOWN_ISIN = "ZZ0000000000"


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
