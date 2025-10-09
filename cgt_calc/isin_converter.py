"""Convert ISIN to tickers using transaction data or manual mappings."""

from __future__ import annotations

import csv
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from requests_ratelimiter import LimiterSession

from .const import CGT_TEST_MODE, INITIAL_ISIN_TRANSLATION_FILE
from .exceptions import ParsingError
from .parsers import ISIN_TRANSLATION_HEADER, read_isin_translation_file
from .resources import RESOURCES_PACKAGE
from .util import is_isin

if TYPE_CHECKING:
    from .model import BrokerTransaction


class IsinConverter:
    """Converter which holds rate history."""

    def __init__(
        self,
        isin_translation_file: str | None = None,
    ):
        """Create the IsinConverter."""
        # https://www.openfigi.com/api/documentation#rate-limits
        self.session = LimiterSession(per_minute=24)
        self.isin_translation_file = isin_translation_file
        self.data: dict[str, set[str]] = read_isin_translation_file(
            resources.files(RESOURCES_PACKAGE).joinpath(INITIAL_ISIN_TRANSLATION_FILE)
        )
        self.write_data: dict[str, set[str]] = {}
        if isin_translation_file is not None and Path(isin_translation_file).is_file():
            self.write_data = read_isin_translation_file(Path(isin_translation_file))
            self.data.update(self.write_data)

        self.validate_data()

    def validate_data(self) -> None:
        """Validate the current ISIN translation data."""

        reverse_cache: dict[str, str] = {}
        for isin, symbols in self.data.items():
            assert is_isin(isin), f"{isin} not a valid ISIN!"
            if symbols == {""}:
                continue
            for symbol in symbols:
                assert symbol, f"Invalid empty ticker for {isin} ISIN"
                assert (symbol not in reverse_cache) or (
                    reverse_cache[symbol] == "ISIN"
                ), (
                    f"Found multiple ISINs {isin},{reverse_cache[symbol]} "
                    f"for the same ticker {symbol}"
                )
                reverse_cache[symbol] = isin

    def add_from_transaction(self, transaction: BrokerTransaction) -> None:
        """Add the ISIN to symbol mapping from an existing transaction."""
        if transaction.symbol and transaction.isin:
            assert is_isin(transaction.isin), (
                f"Not a valid ISIN for transaction {transaction}!"
            )
            assert (
                not self.data.get(transaction.isin)
                or transaction.symbol in self.data[transaction.isin]
            ), (
                f"Inconsistent ISIN value from transaction {transaction} and currently "
                "stored in the mapping {self.data[transaction.isin]}"
            )

            if transaction.symbol not in self.data.get(transaction.isin, set()):
                self.write_data.setdefault(transaction.isin, set())
                self.data.setdefault(transaction.isin, set())
                self.write_data[transaction.isin].add(transaction.symbol)
                self.data[transaction.isin].add(transaction.symbol)
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

    def _write_isin_translation_file(self) -> None:
        self.validate_data()
        if CGT_TEST_MODE or self.isin_translation_file is None:
            return
        with Path(self.isin_translation_file).open("w", encoding="utf8") as fout:
            data_rows = [
                [isin, *sorted(symbols)] for isin, symbols in self.write_data.items()
            ]
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
            msg = f"Error while fetching ISIN information for {isin} "
            if response_text:
                msg += f"Response was: {response_text}"
            msg += "Either try again or if you're sure about the translation you can "
            msg += f"add it manually in {self.isin_translation_file}.\n"
            msg += f"The error was: {err}\n"
            raise ParsingError(url, msg) from err

        if (
            not json_response
            or len(json_response) == 0
            or "data" not in json_response[0]
        ):
            print(
                f"Warning: Couldn't translate ISIN {isin}: "
                f"Invalid Response {json_response}"
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

        print(f"Warning: Couldn't translate ISIN {isin}: Match not found {json_data}")
        return set()
