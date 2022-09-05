"""Convert currencies to GBP using rate history."""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

import requests

from .dates import is_date
from .exceptions import ExchangeRateMissingError, ParsingError
from .model import BrokerTransaction

EXCHANGE_RATES_HEADER: Final = ["month", "currency", "rate"]


class CurrencyConverter:
    """Coverter which holds rate history."""

    def __init__(
        self,
        exchange_rates_file: str | None = None,
        initial_data: dict[str, dict[str, Decimal]] | None = None,
    ):
        """Load data from exchange_rates_file and optionally from initial_data."""
        self.exchange_rates_file = exchange_rates_file
        read_data = self._read_exchange_rates_file(exchange_rates_file)
        self.cache = {**read_data, **(initial_data or {})}

    @staticmethod
    def _read_exchange_rates_file(
        exchange_rates_file: str | None,
    ) -> dict[str, dict[str, Decimal]]:
        cache: dict[str, dict[str, Decimal]] = {}
        if exchange_rates_file is None:
            return cache
        path = Path(exchange_rates_file)
        if not path.is_file():
            return cache
        with path.open(encoding="utf8") as fin:
            csv_reader = csv.DictReader(fin)
            # skip the header
            next(csv_reader)
            for line in csv_reader:
                if sorted(EXCHANGE_RATES_HEADER) != sorted(line.keys()):
                    raise ParsingError(
                        exchange_rates_file,
                        f"invalid columns {line.keys()},"
                        f"they should be {EXCHANGE_RATES_HEADER}",
                    )
                if line["month"] not in cache:
                    cache[line["month"]] = {}
                cache[line["month"]][line["currency"]] = Decimal(line["rate"])
            return cache

    @staticmethod
    def _write_exchange_rates_file(
        exchange_rates_file: str | None, data: dict[str, dict[str, Decimal]]
    ) -> None:
        if exchange_rates_file is None:
            return
        with Path(exchange_rates_file).open("w", encoding="utf8") as fout:
            data_rows = [
                [month, symbol, str(rate)]
                for month, rates in data.items()
                for symbol, rate in rates.items()
            ]
            writer = csv.writer(fout)
            writer.writerows([EXCHANGE_RATES_HEADER] + data_rows)

    def _query_hmrc_api(self, month_str: str) -> None:
        url = (
            "http://www.hmrc.gov.uk/softwaredevelopers/rates/"
            f"exrates-monthly-{month_str}.xml"
        )
        response = requests.get(url, timeout=10)
        if not response.ok:
            raise ParsingError(
                url, f"HMRC API returned a {response.status_code} response"
            )
        tree = ElementTree.fromstring(response.text)
        rates = {
            str(getattr(row.find("currencyCode"), "text", None)).upper(): Decimal(
                str(getattr(row.find("rateNew"), "text", None))
            )
            for row in tree
        }
        if None in rates or None in rates.values():
            raise ParsingError(url, "HMRC API produced invalid/unknown data")
        self.cache[month_str] = rates
        self._write_exchange_rates_file(self.exchange_rates_file, self.cache)

    def currency_to_gbp_rate(self, currency: str, date: datetime.date) -> Decimal:
        """Get GBP/currency rate at given date."""
        assert is_date(date)
        month_str = date.strftime("%m%y")
        if month_str not in self.cache:
            self._query_hmrc_api(month_str)
        if currency not in self.cache[month_str]:
            raise ExchangeRateMissingError(currency, date)
        return self.cache[month_str][currency]

    def to_gbp(self, amount: Decimal, currency: str, date: datetime.date) -> Decimal:
        """Convert amount from given currency to GBP."""
        if currency == "GBP":
            return amount
        return amount / self.currency_to_gbp_rate(currency.upper(), date)

    def to_gbp_for(self, amount: Decimal, transaction: BrokerTransaction) -> Decimal:
        """Convert amount from transaction currency to GBP."""

        return self.to_gbp(amount, transaction.currency, transaction.date)
