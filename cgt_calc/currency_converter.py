"""Convert currencies to GBP using rate history."""

from __future__ import annotations

from collections import defaultdict
import csv
import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Final

from defusedxml import ElementTree as ET
import requests

from .const import CGT_TEST_MODE
from .dates import is_date
from .exceptions import ExchangeRateMissingError, ExternalApiError, ParsingError
from .util import open_with_parents

if TYPE_CHECKING:
    from pathlib import Path

    from .model import BrokerTransaction

EXCHANGE_RATES_HEADER: Final = ["month", "currency", "rate"]
NEW_ENDPOINT_FROM_YEAR: Final = 2021


class CurrencyConverter:
    """Converter which holds rate history."""

    def __init__(
        self,
        exchange_rates_file: Path | None = None,
        initial_data: dict[datetime.date, dict[str, Decimal]] | None = None,
    ):
        """Load data from exchange_rates_file and optionally from initial_data."""
        self.exchange_rates_file = exchange_rates_file
        read_data = self._read_exchange_rates_file(exchange_rates_file)
        self.cache: dict[datetime.date, dict[str, Decimal]] = {
            **read_data,
            **(initial_data or {}),
        }
        self.session = requests.Session()

    @staticmethod
    def _read_exchange_rates_file(
        exchange_rates_file: Path | None,
    ) -> defaultdict[datetime.date, dict[str, Decimal]]:
        cache: defaultdict[datetime.date, dict[str, Decimal]] = defaultdict(dict)
        if exchange_rates_file is None or not exchange_rates_file.is_file():
            return cache
        with exchange_rates_file.open(encoding="utf8") as fin:
            csv_reader = csv.DictReader(fin)
            if csv_reader.fieldnames is None:
                # File is empty.
                return cache
            for row_number, line in enumerate(csv_reader, start=2):
                # Guard against schema drift before touching row contents.
                if sorted(EXCHANGE_RATES_HEADER) != sorted(line.keys()):
                    raise ParsingError(
                        exchange_rates_file,
                        "Unexpected columns in exchange rate file: "
                        f"found {sorted(line.keys())}, expected {EXCHANGE_RATES_HEADER}",
                    )

                # Trim values so that whitespace-only cells count as empty.
                normalized_values = {
                    field: (line[field].strip() if line[field] is not None else "")
                    for field in EXCHANGE_RATES_HEADER
                }

                # Skip harmless blank lines left by editors or tooling.
                if not any(normalized_values.values()):
                    continue

                # Missing values mean we cannot trust the rate entry.
                missing_fields = [
                    field for field, value in normalized_values.items() if not value
                ]
                if missing_fields:
                    raise ParsingError(
                        exchange_rates_file,
                        "Missing data in exchange rate file at line "
                        f"{row_number}: {', '.join(sorted(missing_fields))}",
                    )

                month = normalized_values["month"]
                currency = normalized_values["currency"]
                rate_value = normalized_values["rate"]

                try:
                    date = datetime.date.fromisoformat(month)
                except ValueError as err:
                    raise ParsingError(
                        exchange_rates_file,
                        f"Invalid date '{month}' at line {row_number}",
                    ) from err

                try:
                    rate = Decimal(rate_value)
                except (InvalidOperation, ValueError) as err:
                    raise ParsingError(
                        exchange_rates_file,
                        f"Invalid rate '{rate_value}' at line {row_number}",
                    ) from err

                # Duplicates suggest conflicting data, so fail fast.
                if currency in cache[date]:
                    raise ParsingError(
                        exchange_rates_file,
                        "Duplicate currency entry for "
                        f"{currency} on {month} at line {row_number}",
                    )

                cache[date][currency] = rate
            return cache

    @staticmethod
    def _write_exchange_rates_file(
        exchange_rates_file: Path | None, data: dict[datetime.date, dict[str, Decimal]]
    ) -> None:
        if exchange_rates_file is None or CGT_TEST_MODE:
            return
        with open_with_parents(exchange_rates_file) as fout:
            data_rows = [
                [month, symbol, str(rate)]
                for month, rates in data.items()
                for symbol, rate in rates.items()
            ]
            writer = csv.writer(fout)
            writer.writerows([EXCHANGE_RATES_HEADER, *data_rows])

    def _query_hmrc_api(self, date: datetime.date) -> None:
        # Pre 2021 we need to use the old HMRC endpoint
        if date.year < NEW_ENDPOINT_FROM_YEAR:
            month_str = date.strftime("%m%y")
            url = (
                "http://www.hmrc.gov.uk/softwaredevelopers/rates/"
                f"exrates-monthly-{month_str}.xml"
            )
        else:
            month_str = date.strftime("%Y-%m")
            url = (
                "https://www.trade-tariff.service.gov.uk/uk/api/"
                f"exchange_rates/files/monthly_xml_{month_str}.xml"
            )
        try:
            response = self.session.get(url, timeout=10)
        except Exception as err:
            msg = f"Failed to retrieve HMRC exchange rates for {month_str} from {url}. "
            if self.exchange_rates_file:
                msg += (
                    "Try again later or record the rates manually in "
                    f"{self.exchange_rates_file}. "
                )
            else:
                msg += "Try again later or provide the rates manually. "
            msg += f"Error: {err}"
            raise ExternalApiError(url, msg) from err

        if not response.ok:
            body = response.text.strip()
            extra = ""
            if body:
                snippet_length_limit = 200
                snippet = body[:snippet_length_limit]
                if len(body) > snippet_length_limit:
                    snippet += "..."
                extra = f" Response body: {snippet}"
            raise ExternalApiError(
                url,
                f"HMRC API returned HTTP {response.status_code} for {month_str}.{extra}",
            )

        tree = ET.fromstring(response.text)
        rates = {
            str(getattr(row.find("currencyCode"), "text", None)).upper(): Decimal(
                str(getattr(row.find("rateNew"), "text", None))
            )
            for row in tree
        }
        if None in rates or None in rates.values():
            raise ExternalApiError(
                url,
                f"HMRC API response for {month_str} is missing expected currency data",
            )
        self.cache[date] = rates
        self._write_exchange_rates_file(self.exchange_rates_file, self.cache)

    def currency_to_gbp_rate(self, currency: str, date: datetime.date) -> Decimal:
        """Get GBP/currency rate at given date."""
        assert is_date(date)
        if date not in self.cache:
            self._query_hmrc_api(date)
        if currency not in self.cache[date]:
            raise ExchangeRateMissingError(currency, date)
        return self.cache[date][currency]

    def to_gbp(self, amount: Decimal, currency: str, date: datetime.date) -> Decimal:
        """Convert amount from given currency to GBP."""
        if currency == "GBP":
            return amount
        return amount / self.currency_to_gbp_rate(currency.upper(), date)

    def to_gbp_for(self, amount: Decimal, transaction: BrokerTransaction) -> Decimal:
        """Convert amount from transaction currency to GBP."""
        return self.to_gbp(amount, transaction.currency, transaction.date)
