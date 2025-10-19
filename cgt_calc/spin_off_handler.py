"""Convert currencies to GBP using rate history."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .exceptions import ParsingError

if TYPE_CHECKING:
    import datetime

    from .model import Position

CHOICES_TO_SHOW: Final = 10
SPIN_OFFS_HEADER: Final = ["dst", "src"]
LOGGER = logging.getLogger(__name__)


class SpinOffHandler:
    """Handles spin-offs."""

    def __init__(
        self,
        spin_offs_file: Path | None = None,
    ):
        """Load data from spin_offs_file and optionally from initial_data."""
        self.spin_offs_file = spin_offs_file
        read_data = self._read_spin_offs_file()
        self.cache = read_data

    def _read_spin_offs_file(self) -> dict[str, str]:
        cache: dict[str, str] = {}
        if self.spin_offs_file is None or not self.spin_offs_file.is_file():
            return cache

        with self.spin_offs_file.open(encoding="utf8") as fin:
            csv_reader = csv.DictReader(fin)
            for line in csv_reader:
                if sorted(SPIN_OFFS_HEADER) != sorted(line.keys()):
                    raise ParsingError(
                        self.spin_offs_file,
                        f"invalid columns {line.keys()}, "
                        f"they should be {SPIN_OFFS_HEADER}",
                    )
                cache[line["dst"]] = line["src"]
            return cache

    def _write_spin_off_file(self) -> None:
        if self.spin_offs_file is None:
            return
        with self.spin_offs_file.open("w", encoding="utf8") as fout:
            data_rows = [[dst, src] for dst, src in self.cache.items()]
            writer = csv.writer(fout)
            writer.writerows([SPIN_OFFS_HEADER, *data_rows])

    def get_spin_off_source(
        self, symbol: str, date: datetime.date, portfolio: dict[str, Position]
    ) -> str:
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
            LOGGER.error(
                "Invalid ticker: %s, couldn't find it in the portfolio!", ticker
            )
            if len(portfolio) > CHOICES_TO_SHOW:
                LOGGER.info(
                    "Available choices (showing %d): %s",
                    CHOICES_TO_SHOW,
                    sorted(portfolio)[:CHOICES_TO_SHOW],
                )
            else:
                LOGGER.info("Available choices: %s", sorted(portfolio))
        self.cache[symbol] = ticker
        self._write_spin_off_file()
        return ticker
