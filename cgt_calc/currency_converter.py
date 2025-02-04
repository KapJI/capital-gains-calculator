"""Convert currencies to GBP using rate history."""

from __future__ import annotations

from collections import defaultdict
import csv
import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Final

from defusedxml import ElementTree as ET
import pycountry
import requests

from .dates import is_date
from .exceptions import ExchangeRateMissingError, ParsingError
from .model import ExchangeRateType

if TYPE_CHECKING:
    from .model import BrokerTransaction

EXCHANGE_RATES_HEADER: Final = ["month", "currency", "rate"]
NEW_ENDPOINT_FROM_YEAR: Final = 2021


class CurrencyConverter:
    """Converter which holds rate history."""

    def __init__(
        self,
        exchange_rates_file: str | None = None,
        initial_data: dict[datetime.date, dict[str, Decimal]] | None = None,
        rate_type: ExchangeRateType = ExchangeRateType.MONTHLY,
    ):
        """Load data from exchange_rates_file and optionally from initial_data."""
        self.exchange_rates_file = exchange_rates_file
        read_data = self._read_exchange_rates_file(exchange_rates_file)
        self.cache: dict[datetime.date, dict[str, Decimal]] = {
            **read_data,
            **(initial_data or {}),
        }
        self.session = requests.Session()
        self.rate_type = rate_type

    @staticmethod
    def _read_exchange_rates_file(
        exchange_rates_file: str | None,
    ) -> defaultdict[datetime.date, dict[str, Decimal]]:
        cache: defaultdict[datetime.date, dict[str, Decimal]] = defaultdict(dict)
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
                        f"invalid columns {line.keys()}, "
                        f"they should be {EXCHANGE_RATES_HEADER}",
                    )
                date = datetime.date.fromisoformat(line["month"])
                cache[date][line["currency"]] = Decimal(line["rate"])
            return cache

    @staticmethod
    def _write_exchange_rates_file(
        exchange_rates_file: str | None, data: dict[datetime.date, dict[str, Decimal]]
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
            writer.writerows([EXCHANGE_RATES_HEADER, *data_rows])

    def _query_hmrc_api(self, date: datetime.date) -> None:
        """Query HMRC API for exchange rates."""

        if self.rate_type == ExchangeRateType.MONTHLY:
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
                    "https://www.trade-tariff.service.gov.uk/api/v2/"
                    f"exchange_rates/files/monthly_xml_{month_str}.xml"
                )
        else:  # ExchangeRateType.ANNUAL
            year_str = date.strftime("%Y")
            month_str = "12"  # Annual rates are published in December
            url = (
                "https://www.trade-tariff.service.gov.uk/api/v2/"
                f"exchange_rates/files/average_csv_{year_str}-{month_str}.csv"
            )

        try:
            response = self.session.get(url, timeout=10)
        except Exception as err:
            month_str = date.strftime("%Y-%m")
            msg = f"Error while fetching HMRC {self.rate_type.value} exchange rates"
            msg += f"for {month_str} from the following url: {url}.\n"
            msg += "Either try again or if you're sure about the rates you can "
            msg += f"add them manually in {self.exchange_rates_file}.\n"
            msg += f"The error was: {err}\n"
            raise ParsingError(url, msg) from err

        if not response.ok:
            raise ParsingError(
                url, f"HMRC API returned a {response.status_code} response"
            )

        if self.rate_type == ExchangeRateType.MONTHLY:
            tree = ET.fromstring(response.text)
            rates = {
                str(getattr(row.find("currencyCode"), "text", None)).upper(): Decimal(
                    str(getattr(row.find("rateNew"), "text", None))
                )
                for row in tree
            }
        else:  # ExchangeRateType.ANNUAL
            csv_data = csv.DictReader(response.text.splitlines())
            rates = self.parse_annual_rates_csv(csv_data)

        if None in rates or None in rates.values():
            raise ParsingError(url, "HMRC API produced invalid/unknown data")
        self.cache[date] = rates
        self._write_exchange_rates_file(self.exchange_rates_file, self.cache)

    def parse_annual_rates_csv(
        self, csv_data: csv.DictReader[str]
    ) -> dict[str, Decimal]:
        """Parse annual rates CSV data from HMRC."""
        rates = {}
        for row in csv_data:
            try:
                if "Currency Code" in row:
                    # Use currencyCode if available
                    currency = row["Currency Code"].strip().upper()
                    rate = Decimal(row["Currency Units per £1"])
                    rates[currency] = rate
                else:
                    # Fall back to checking country name
                    first_col = next(iter(row))
                    country_name = row[first_col].strip()
                    rate = Decimal(row["Currency Units per pound"])

                    # Try to find country by name or code
                    country = pycountry.countries.get(
                        name=country_name
                    ) or pycountry.countries.get(alpha_3=country_name)
                    if country:
                        currency = pycountry.currencies.get(numeric=country.numeric)
                        if currency:
                            rates[currency.alpha_3] = rate
            except (KeyError, InvalidOperation, AttributeError):  # noqa: PERF203
                continue
        return rates

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
