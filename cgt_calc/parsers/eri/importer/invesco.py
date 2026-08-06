"""Invesco ERI transaction parser."""

from __future__ import annotations

from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING

import dateutil.parser as date_parser
import pdfplumber

from cgt_calc.util import is_isin, round_decimal

if TYPE_CHECKING:
    import datetime
    from pathlib import Path

from cgt_calc.parsers.eri.model import ERITransaction

from .model import ERIImporter, ERIImporterOutput

LOGGER = logging.getLogger(__name__)

REPORT_FILE_REGEX = re.compile(r"^invesco-.*reportable-income.*(\d+)\.pdf$")
CURRENCY_REGEX = re.compile(r"^[A-Z]{3}$")
AMOUNT_REGEX = re.compile(r"^\d+\.\d+$")

ISIN_COLUMN = 0
CURRENCY_COLUMN = 1
ERI_COLUMN = 2
MIN_VALID_YEAR = 2018

INVESCO_ERI_FILENAME = "invesco_eri.csv"


class InvescoImporter(ERIImporter):
    """Parser for Invesco ERI spreadsheets."""

    def __init__(self) -> None:
        """Create a new Invesco Parser instance."""
        super().__init__(name="Invesco")

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
        date_str = matches[0][len(prefix) :].strip().replace("Decemeber", "December")
        if date_str.startswith("ed"):
            date_str = date_str[2:].strip()
        try:
            date = date_parser.parse(date_str, fuzzy=True, dayfirst=True)
        except date_parser.ParserError:
            return None
        return date.date()

    @staticmethod
    def _extract_header(
        page: pdfplumber.page.Page,
    ) -> tuple[tuple[float, float, float, float], list[float], dict[int, int]]:
        # Useful for debugging:
        # page.to_image(resolution=100).debug_tablefinder().save(f"full_p{page_num}.png")
        header_table = page.find_table()
        assert header_table
        cur_header = [
            (col or "").replace("\n", " ").strip() for col in header_table.extract()[0]
        ]
        LOGGER.debug("cur_header=%s", cur_header)
        colmap = {}
        colreg = {
            ISIN_COLUMN: re.compile(r"^ISIN( / Identifier)?.*$"),
            CURRENCY_COLUMN: re.compile(r"^CURRENCY OF SHARE CLASS.*$", re.IGNORECASE),
            ERI_COLUMN: re.compile(
                r"^(PER UNIT EXCESS REPORTABLE INCOME OVER DISTRIBUTIONS|PIR\(1 NE E\) CR S\(3 OP U \)E M \( N C4\) ET IT OO EVF X EC TRH E S EDS IR SR ETE PRP OIOBR RUT TTIN AIOG BNL P ESE IRNIO D).*$",
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
        min_column_count = 11
        assert len(cur_header) >= min_column_count
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
        # Useful for debugging:
        # cropped.to_image(resolution=100).debug_tablefinder(table_settings).save(f"cropped_p{page_num}.png")
        data_table = cropped.extract_table(table_settings)
        assert data_table
        return data_table

    @staticmethod
    def _process_data_table(
        page_num: int,
        data_table: list[list[str | None]],
        reporting_period_end: datetime.date,
        colmap: dict[int, int],
    ) -> list[ERITransaction]:
        transactions = []
        for row_num, raw_row in enumerate(data_table, 1):
            row = {}
            for col, pos in colmap.items():
                row[col] = (raw_row[pos] or "").strip()
            isin = row[ISIN_COLUMN]
            if not isin:
                # probably a subrow
                continue
            if re.match(r"(\*\*? Distribution|Note 1).*", raw_row[0] or ""):
                # not part of the table
                continue
            assert is_isin(isin), (
                f"Bad ISIN in page {page_num}, row {row_num}: {row[ISIN_COLUMN]}"
            )
            currency = row[CURRENCY_COLUMN].replace("JPN", "JPY")
            assert re.match(CURRENCY_REGEX, row[CURRENCY_COLUMN]), (
                f"Bad currency in page {page_num}, row {row_num}: {row[CURRENCY_COLUMN]}"
            )
            assert re.match(AMOUNT_REGEX, row[ERI_COLUMN]), (
                f"Bad amount in page {page_num}, row {row_num}: {row[ERI_COLUMN]}"
            )
            amount = round_decimal(Decimal(row[ERI_COLUMN]), 5)
            transactions.append(
                ERITransaction(
                    isin=isin,
                    date=reporting_period_end,
                    price=amount,
                    currency=currency,
                )
            )
        LOGGER.info("Read %d rows from page %d", len(transactions), page_num)
        return transactions

    @staticmethod
    # v1 has headers only on the first page
    def _parse_v1(pdf: pdfplumber.pdf.PDF) -> list[ERITransaction] | None:
        first_page = pdf.pages[0]
        prefix = "STATEMENT OF REPORTABLE INCOME FOR INVESCO MARKETS II PLC FOR THE PERIOD ENDED"
        if not first_page.search(prefix):
            return None
        LOGGER.info("Detected v1 format")
        reporting_period_end = InvescoImporter._extract_report_end_date(
            first_page, prefix
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            LOGGER.warning("First page has old or missing reporting date")
            return []
        header_bbox, columns, colmap = InvescoImporter._extract_header(first_page)
        (h_left, _h_top, h_right, h_bottom) = header_bbox
        transactions = []
        for page_num, page in enumerate(pdf.pages, 1):
            if page_num == 1:
                cropped = page.crop((h_left, h_bottom, h_right, page.height))
            else:
                cropped = page.crop((0, 0, page.width, page.height))
            data_table = InvescoImporter._extract_data_rows(page_num, cropped, columns)
            transactions += InvescoImporter._process_data_table(
                page_num, data_table, reporting_period_end, colmap
            )
        return transactions

    @staticmethod
    # v2 has headers on all pages, and the headers are slightly different
    def _parse_v2(pdf: pdfplumber.pdf.PDF) -> list[ERITransaction] | None:
        first_page = pdf.pages[0]
        if not first_page.search("Invesco Markets II plc"):
            return None
        if not first_page.search(
            "UK reporting fund status Report to Investor"
        ) and not first_page.search("UK reporting fund status report to investors"):
            return None
        LOGGER.info("Detected v2 format")
        reporting_period_end = InvescoImporter._extract_report_end_date(
            first_page, "Reporting Period End"
        )
        if reporting_period_end is None or reporting_period_end.year < MIN_VALID_YEAR:
            LOGGER.warning("First page has old or missing reporting date")
            return []
        transactions = []
        for page_num, page in enumerate(pdf.pages, 1):
            header_bbox, columns, colmap = InvescoImporter._extract_header(page)
            (h_left, _h_top, h_right, h_bottom) = header_bbox
            cropped = page.crop((h_left, h_bottom, h_right, page.height))
            data_table = InvescoImporter._extract_data_rows(page_num, cropped, columns)
            transactions += InvescoImporter._process_data_table(
                page_num, data_table, reporting_period_end, colmap
            )
        return transactions

    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse a Invesco ERI file."""
        if not REPORT_FILE_REGEX.match(file.name):
            return None

        result = ERIImporterOutput(
            transactions=[], output_file_name=INVESCO_ERI_FILENAME
        )

        with pdfplumber.open(file) as pdf:
            if not pdf.pages:
                return None
            parsers = [InvescoImporter._parse_v1, InvescoImporter._parse_v2]
            for parser in parsers:
                parsed_data = parser(pdf)
                if parsed_data is not None:
                    result.transactions = parsed_data
                    break
            else:
                return None
        LOGGER.info("Done parsing")
        return result
