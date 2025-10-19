"""Convert ISIN to tickers using transaction data or manual mappings."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from importlib import resources
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from requests_ratelimiter import LimiterSession

from .const import CGT_TEST_MODE, INITIAL_ISIN_TRANSLATION_RESOURCE
from .exceptions import (
    ExternalApiError,
    InvalidTransactionError,
    IsinTranslationError,
    ParsingError,
    UnexpectedColumnCountError,
)
from .resources import RESOURCES_PACKAGE
from .util import is_isin, open_with_parents

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from .model import BrokerTransaction

ISIN_TRANSLATION_HEADER: Final = ["ISIN", "symbol"]
ISIN_TRANSLATION_COLUMNS_NUM: Final = len(ISIN_TRANSLATION_HEADER)
LOGGER = logging.getLogger(__name__)


@dataclass
class IsinTranslationEntry:
    """Entry from ISIN Translation file."""

    isin: str
    symbols: set[str]

    def __init__(self, row: list[str], file: Path):
        """Create entry from CSV row."""
        if len(row) < ISIN_TRANSLATION_COLUMNS_NUM:
            raise UnexpectedColumnCountError(row, ISIN_TRANSLATION_COLUMNS_NUM, file)
        self.isin = row[0]
        if not is_isin(self.isin):
            raise ParsingError(file, f"Row contains invalid ISIN '{self.isin}'")
        self.symbols = set(row[1:])

    def __str__(self) -> str:
        """Return string representation."""
        return f"ISIN: {self.isin}, symbol: {self.symbols}"


class IsinConverter:
    """Converter which holds rate history."""

    def __init__(
        self,
        isin_translation_file: Path | None = None,
    ):
        """Create the IsinConverter."""
        # https://www.openfigi.com/api/documentation#rate-limits
        self.session = LimiterSession(per_minute=24)
        self.isin_translation_file = isin_translation_file
        self.data: dict[str, set[str]] = {}
        self.write_data: dict[str, set[str]] = {}
        self._read_isin_translation_data()
        self.validate_data()

    def validate_data(self) -> None:
        """Validate the current ISIN translation data."""

        reverse_cache: dict[str, str] = {}
        for isin, symbols in self.data.items():
            if not is_isin(isin):
                raise IsinTranslationError(
                    f"Invalid ISIN found in translation data: {isin}"
                )
            for symbol in symbols:
                if not symbol:
                    raise IsinTranslationError(
                        f"Ticker list for ISIN {isin} contains an empty value"
                    )
                existing_isin = reverse_cache.get(symbol)
                if existing_isin and existing_isin != isin:
                    raise IsinTranslationError(
                        f"Ticker {symbol} already linked to ISIN {existing_isin}; "
                        f"cannot also link to {isin}"
                    )
                reverse_cache[symbol] = isin

    def add_from_transaction(self, transaction: BrokerTransaction) -> None:
        """Add the ISIN to symbol mapping from an existing transaction."""
        if transaction.symbol and transaction.isin:
            if not is_isin(transaction.isin):
                raise InvalidTransactionError(
                    transaction,
                    f"Transaction uses invalid ISIN {transaction.isin}",
                )
            current_symbols = self.data.get(transaction.isin)
            if current_symbols and transaction.symbol not in current_symbols:
                raise InvalidTransactionError(
                    transaction,
                    f"Ticker {transaction.symbol} does not match existing mapping: "
                    f"ISIN {transaction.isin} is linked to {', '.join(sorted(current_symbols))}",
                )

            if transaction.symbol not in self.data.get(transaction.isin, set()):
                self.data.setdefault(transaction.isin, set()).add(transaction.symbol)
                self.write_data.setdefault(transaction.isin, set()).add(
                    transaction.symbol
                )
                self._write_isin_translation_file()

    def get_symbols(self, isin: str) -> set[str]:
        """Return the symbol associated with the input ISIN or empty string."""
        result = self.data.get(isin)
        if result is None:
            result = self._fetch_live(isin)
            self.data[isin] = result
            if result:
                self.write_data[isin] = result
                self._write_isin_translation_file()
        return result

    def _read_isin_translation_data(self) -> None:
        """Read ISIN translation data from bundled and user-provided sources."""

        def load(source: Traversable | Path) -> dict[str, set[str]]:
            """Load ISIN translation data from a CSV source."""
            file_label = (
                source if isinstance(source, Path) else Path("resources") / source.name
            )
            with source.open(encoding="utf-8") as csv_file:
                lines = list(csv.reader(csv_file))
            if not lines:
                return {}
            header = lines[0]
            if header != ISIN_TRANSLATION_HEADER:
                raise ParsingError(
                    file_label,
                    "Unexpected header in ISIN translation data: "
                    f"expected {ISIN_TRANSLATION_HEADER}, found {header}",
                )
            entries: dict[str, set[str]] = {}
            for row in lines[1:]:
                entry = IsinTranslationEntry(row, file_label)
                entries[entry.isin] = entry.symbols
            return entries

        bundled_source = resources.files(RESOURCES_PACKAGE).joinpath(
            INITIAL_ISIN_TRANSLATION_RESOURCE
        )
        self.data.update(load(bundled_source))

        if (
            self.isin_translation_file is not None
            and self.isin_translation_file.is_file()
        ):
            self.write_data = load(self.isin_translation_file)
            self.data.update(self.write_data)

    def _write_isin_translation_file(self) -> None:
        self.validate_data()
        if self.isin_translation_file is None or CGT_TEST_MODE:
            return
        with open_with_parents(self.isin_translation_file) as fout:
            data_rows = [[isin, *symbols] for isin, symbols in self.write_data.items()]
            writer = csv.writer(fout)
            writer.writerows([ISIN_TRANSLATION_HEADER, *data_rows])

    def _fetch_live(self, isin: str) -> set[str]:
        url = "https://api.openfigi.com/v3/mapping"
        headers = {"Content-type": "application/json"}
        data = [{"idType": "ID_ISIN", "idValue": isin}]
        response_text = ""
        try:
            response = self.session.post(url, json=data, headers=headers, timeout=10)
            response_text = response.text
            json_response = response.json()
        except Exception as err:
            msg = f"Failed to fetch ISIN information for {isin}. "
            if response_text:
                msg += f"Server response: {response_text}. "
            msg += "Try again later or, if you're confident about the ticker, add it "
            msg += f"manually to {self.isin_translation_file}. Error: {err}"
            raise ExternalApiError(url, msg) from err

        if (
            not json_response
            or len(json_response) == 0
            or "data" not in json_response[0]
        ):
            LOGGER.warning(
                "Couldn't translate ISIN %s: Invalid Response: %s", isin, json_response
            )
            return set()

        json_data = json_response[0]["data"]

        # https://www.openfigi.com/assets/content/OpenFIGI_Exchange_Codes-3d3e5936ba.csv
        # Get london exchange first
        result = {data["ticker"] for data in json_data if data["exchCode"] == "LN"}

        if result:
            return result

        # Get all the other UK exchanges
        result = {
            data["ticker"]
            for data in json_data
            if data["exchCode"] in ("LC", "LT", "LI", "LO")
        }

        if result:
            return result

        # Get the shorter ticker as final fallback
        all_tickers = [data["ticker"] for data in json_data if data]
        if all_tickers:
            return {min(all_tickers, key=len)}

        LOGGER.warning(
            "Couldn't translate ISIN %s: Match not found in %s", isin, json_data
        )
        return set()
