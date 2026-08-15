"""Xtrackers ERI transaction parser."""

from __future__ import annotations

from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING

import dateutil.parser as date_parser
import pdfplumber

from cgt_calc.parsers.eri.model import ERITransaction
from cgt_calc.util import is_isin, round_decimal

from .model import ERIImporter, ERIImporterOutput

if TYPE_CHECKING:
    import datetime
    from pathlib import Path

LOGGER = logging.getLogger(__name__)

REPORT_FILE_REGEX = re.compile(r"^Xtrackers.*Report.*\.pdf$")

XTRACKERS_ERI_FILENAME = "xtrackers_eri.csv"

CURRENCY_REGEX = re.compile(r"^[A-Z]{3}$")
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

# Column indices configuration for Xtrackers tables
ISIN_COLUMN = 0
CURRENCY_COLUMN = 1
ERI_COLUMN = 2
MIN_VALID_YEAR = 2018


class XtrackersImporter(ERIImporter):
    """Parser for Xtrackers ERI spreadsheets."""

    def __init__(self) -> None:
        """Create a new Xtrackers Parser instance."""
        super().__init__(name="Xtrackers")

    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse the input PDF file and return ERI transactions."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None

        result = ERIImporterOutput(
            transactions=[], output_file_name=XTRACKERS_ERI_FILENAME
        )

        with pdfplumber.open(file) as pdf:
            if not pdf.pages:
                return None
            parsers = [
                XtrackersImporter._parse_format,
                XtrackersImporter._parse_ie_format,
            ]
            for parser in parsers:
                parsed_data = parser(pdf)
                if parsed_data is not None:
                    result.transactions = parsed_data
                    break
            else:
                return None
        LOGGER.info("Done parsing Xtrackers file")
        return result

    @staticmethod
    def _extract_report_end_date(
        page: pdfplumber.page.Page, prefix: str
    ) -> datetime.date | None:
        matches = [
            line["text"]
            for line in page.extract_text_lines()
            if prefix.lower() in line["text"].lower()
        ]
        if not matches:
            return None
        date_str = matches[0][len(prefix) :].strip()
        try:
            date = date_parser.parse(date_str, fuzzy=True, dayfirst=True)
        except date_parser.ParserError:
            return None
        return date.date()

    @staticmethod
    def _extract_header(
        page: pdfplumber.page.Page,
    ) -> tuple[tuple[float, float, float, float], list[float], dict[int, int]]:
        header_table = page.find_table()
        assert header_table, "Could not find table headers on the page"
        cur_header = [
            (col or "").replace("\n", " ").strip() for col in header_table.extract()[0]
        ]
        LOGGER.debug("cur_header=%s", cur_header)
        colmap = {}
        colreg = {
            ISIN_COLUMN: re.compile(r"^ISIN.*$", re.IGNORECASE),
            CURRENCY_COLUMN: re.compile(
                r"^(Share class currency|CURRENCY OF THE FOLLOWING AMOUNTS).*$",
                re.IGNORECASE,
            ),
            ERI_COLUMN: re.compile(
                r"^(Excess reported income per share.*|PER UNIT EXCESS REPORTABLE INCOME OVER DISTRIBUTIONS.*)$",
                re.IGNORECASE,
            ),
        }
        for col, regex in colreg.items():
            match_idx = [i for i, h in enumerate(cur_header) if regex.match(h)]
            assert len(match_idx) <= 1, (
                f"Multiple columns matching for {regex}: {match_idx}"
            )
            assert match_idx, f"No column found for {regex}"
            colmap[col] = match_idx[0]

        columns = [col.bbox[0] for col in header_table.columns]
        columns.append(header_table.rows[0].bbox[2])
        return header_table.rows[0].bbox, columns, colmap

    @staticmethod
    def _extract_data_rows(
        page_num: int, cropped: pdfplumber.page.CroppedPage, columns: list[float]
    ) -> list[list[str | None]]:
        table_settings = {
            "vertical_strategy": "explicit",
            "explicit_vertical_lines": columns,
            "horizontal_strategy": "text",
        }
        data_table = cropped.extract_table(table_settings)
        assert data_table
        return data_table

    @staticmethod
    def _process_data_table(
        page_num: int,
        data_table: list[list[str | None]],
        default_reporting_end: datetime.date,
        colmap: dict[int, int],
        is_ie_format: bool = False,
    ) -> list[ERITransaction]:
        transactions = []
        for _, raw_row in enumerate(data_table, 1):
            row = {}
            for col, pos in colmap.items():
                if pos < len(raw_row):
                    row[col] = (raw_row[pos] or "").strip()
                else:
                    row[col] = ""

            isin = row.get(ISIN_COLUMN, "")
            if not isin or not is_isin(isin):
                continue

            currency = row.get(CURRENCY_COLUMN, "").replace("JPN", "JPY")
            if not re.match(CURRENCY_REGEX, currency):
                continue

            eri_str = row.get(ERI_COLUMN, "")
            if not re.match(AMOUNT_REGEX, eri_str):
                continue

            amount = round_decimal(Decimal(eri_str), 5)
            if amount <= 0:
                continue

            transactions.append(
                ERITransaction(
                    isin=isin,
                    date=default_reporting_end,
                    price=amount,
                    currency=currency,
                )
            )
        LOGGER.info("Read %d rows from page %d", len(transactions), page_num)
        return transactions

    @staticmethod
    def _parse_format(pdf: pdfplumber.pdf.PDF) -> list[ERITransaction] | None:
        first_page = pdf.pages[0]
        prefix = "Period ended"
        if not first_page.search("Summary of reportable income calculations"):
            return None
        LOGGER.info("Detected Xtrackers format")

        reporting_period_end = XtrackersImporter._extract_report_end_date(
            first_page, prefix
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            return []

        transactions = []
        for page_num, page in enumerate(pdf.pages, 1):
            header_bbox, columns, colmap = XtrackersImporter._extract_header(page)
            (h_left, _h_top, h_right, h_bottom) = header_bbox
            cropped = page.crop((h_left, h_bottom, h_right, page.height))
            data_table = XtrackersImporter._extract_data_rows(
                page_num, cropped, columns
            )
            transactions += XtrackersImporter._process_data_table(
                page_num, data_table, reporting_period_end, colmap, is_ie_format=False
            )
        return transactions

    @staticmethod
    def _parse_ie_format(pdf: pdfplumber.pdf.PDF) -> list[ERITransaction] | None:
        first_page = pdf.pages[0]
        if not first_page.search(r"Xtrackers \(IE\) Plc"):
            return None
        LOGGER.info("Detected Xtrackers IE format")

        reporting_period_end = XtrackersImporter._extract_report_end_date(
            first_page, "Period of account ended"
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            return []

        transactions = []
        for page_num, page in enumerate(pdf.pages, 1):
            header_bbox, columns, colmap = XtrackersImporter._extract_header(page)
            (h_left, _h_top, h_right, h_bottom) = header_bbox
            cropped = page.crop((h_left, h_bottom, h_right, page.height))
            data_table = XtrackersImporter._extract_data_rows(
                page_num, cropped, columns
            )
            transactions += XtrackersImporter._process_data_table(
                page_num, data_table, reporting_period_end, colmap, is_ie_format=True
            )
        return transactions
