"""Convert currencies to GBP using rate history."""

from __future__ import annotations

from collections import defaultdict
import csv
import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

import requests

from .dates import is_date
from .exceptions import ExchangeRateMissingError, ParsingError
from .model import BrokerTransaction, SpinOff

SPIN_OFFS_HEADER: Final = ["dst", "src"]


class SpinOffHandler:
    """Handles spin-offs."""

    def __init__(
        self,
        spin_offs_file: str | None = None,
    ):
        """Load data from spin_offs_file and optionally from initial_data."""
        self.spin_offs_file = spin_offs_file 
        read_data = self._read_spin_offs_file()
        self.cache = read_data

    def _read_spin_offs_file(self) -> dict[str, str]:
        cache = {}
        if self.spin_offs_file is None:
            return cache

        path = Path(self.spin_offs_file)
        if not path.is_file():
            return cache
        with path.open(encoding="utf8") as fin:
            csv_reader = csv.DictReader(fin)
            for line in csv_reader:
                if sorted(SPIN_OFFS_HEADER) != sorted(line.keys()):
                    raise ParsingError(
                        exchange_rates_file,
                        f"invalid columns {line.keys()}, "
                        f"they should be {SPIN_OFFS_HEADER}",
                    )
                cache[line["dst"]] = line["src"]
            return cache

    def _write_spin_off_file(self) -> None:
        if self.spin_offs_file is None:
            return
        with Path(self.spin_offs_file).open("w", encoding="utf8") as fout:
            data_rows = [
                [dst, src]
                for dst, src in self.cache.items()
            ]
            writer = csv.writer(fout)
            writer.writerows([SPIN_OFFS_HEADER, *data_rows])

    def get_spin_off_source(self, symbol: str, date: datetime.date, portfolio: dict[str, Any]) -> str:
        """Given a spin-off ticker gets the spin-off source."""
        if symbol in self.cache:
            return self.cache[symbol]

        while True:
            # This would ideally be fetched from some stock DB but yfinance does not
            # provide any info on SpinOffs
            ticker = input(
                "For a spin off, please enter the original ticker from which the new "
                f"stock (symbol: {symbol}) was spinned off on {date}: "
            )
            if ticker in portfolio:
                break
            print(f"Invalid ticker: {ticker}, couldn't find it in the portfolio!")
            if len(portfolio) <= 10:
                print(f"Available choices: {sorted(portfolio)}")
        self.cache[symbol] = ticker
        self._write_spin_off_file()
        return ticker
