"""Tests for the Invesco ERI importer."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cgt_calc.parsers.eri.importer.invesco import InvescoImporter
from cgt_calc.parsers.eri.importer.model import ERIImporter
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.parsers.eri.model import ERITransaction

# Wide page so every cell stays on a single line and the drawn grid
# is easy for pdfplumber to detect.
PAGESIZE = (2400, 700)

HEADER = [
    "ISIN / Identifier",
    "CURRENCY OF SHARE CLASS",
    "PER UNIT EXCESS REPORTABLE INCOME OVER DISTRIBUTIONS",
    "FUND NAME",
    "SHARE CLASS",
    "SEDOL",
    "DISTRIBUTION PER UNIT",
    "REPORTING PERIOD START",
    "REPORTING PERIOD END",
    "EQUALISATION PER UNIT",
    "NOTES",
]

DATA_ROWS: list[list[str]] = [
    [
        "IE00B3RBWM25",
        "USD",
        "1.23456",
        "Fund A",
        "Acc",
        "B3RBWM2",
        "0.1",
        "a",
        "b",
        "0.2",
        "n1",
    ],
    # Sub-row without an ISIN, e.g. a share class detail line.
    ["", "", "", "Fund A sub-class", "Hdg", "AAAAAAA", "0.1", "a", "b", "0.2", "n2"],
    # JPN is a known data bug in the reports and is read as JPY.
    [
        "IE00BK5BQV03",
        "JPN",
        "0.55555",
        "Fund B",
        "Dist",
        "BK5BQV0",
        "0.1",
        "a",
        "b",
        "0.2",
        "n3",
    ],
    # Footnote row that is not part of the data table.
    ["** Distribution reinvested", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x"],
]


def _build_pdf(path: Path, preamble: list[str], with_table: bool = True) -> Path:
    """Build a PDF with preamble text lines and optionally a data table."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=PAGESIZE)
    story: list[Flowable] = [Paragraph(line, styles["Normal"]) for line in preamble]
    if with_table:
        story.append(Spacer(1, 20))
        # The third column is wide enough for its long header to stay
        # within the cell boundaries.
        col_widths = [180.0] * len(HEADER)
        col_widths[2] = 340.0
        table = Table([HEADER, *DATA_ROWS], colWidths=col_widths)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    # Keep text within pdfplumber's intersection
                    # tolerance of the column edges, so the outermost
                    # columns stay part of the detected table.
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(table)
    doc.build(story)
    return path


def _check_result_transactions(
    transactions: list[ERITransaction], period_end: datetime.date
) -> None:
    """Assert the transactions parsed from DATA_ROWS."""
    assert len(transactions) == 2
    check_transaction(
        transactions[0], "IE00B3RBWM25", period_end, Decimal("1.23456"), "USD"
    )
    check_transaction(
        transactions[1], "IE00BK5BQV03", period_end, Decimal("0.55555"), "JPY"
    )


def test_parse_v1_report(tmp_path: Path) -> None:
    """Parse a v1 report with the statement header on the first page."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        [
            (
                "STATEMENT OF REPORTABLE INCOME FOR INVESCO MARKETS II PLC "
                "FOR THE PERIOD ENDED 30 June 2022"
            ),
        ],
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "invesco_eri.csv"
    _check_result_transactions(result.transactions, datetime.date(2022, 6, 30))


def test_parse_v2_report(tmp_path: Path) -> None:
    """Parse a v2 report with per-page headers."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status report to investors",
            "Reporting Period End 31 December 2023",
        ],
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    _check_result_transactions(result.transactions, datetime.date(2023, 12, 31))


def test_old_report_is_ignored(tmp_path: Path) -> None:
    """Reports before the minimum valid year produce no transactions."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2015.pdf",
        [
            (
                "STATEMENT OF REPORTABLE INCOME FOR INVESCO MARKETS II PLC "
                "FOR THE PERIOD ENDED 30 June 2015"
            ),
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_v2_alternative_title_and_old_date(tmp_path: Path) -> None:
    """Accept the alternative v2 title and skip old reporting periods."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2015.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status Report to Investor",
            "Reporting Period Ended 31 December 2015",
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_v2_missing_reporting_date(tmp_path: Path) -> None:
    """Produce no transactions when the reporting date line is missing."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status report to investors",
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_v2_unparseable_reporting_date(tmp_path: Path) -> None:
    """Produce no transactions when the reporting date cannot be parsed."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status report to investors",
            "Reporting Period End soon",
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_unknown_content_is_not_accepted(tmp_path: Path) -> None:
    """Return None when the content matches neither format."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        ["Some other document"],
        with_table=False,
    )

    assert InvescoImporter().parse(file) is None


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files with other names."""
    file = _build_pdf(tmp_path / "vanguard.pdf", ["Anything"], with_table=False)

    assert InvescoImporter().parse(file) is None


def test_base_importer_is_abstract(tmp_path: Path) -> None:
    """The base importer does not implement parse."""
    with pytest.raises(NotImplementedError):
        ERIImporter(name="base").parse(tmp_path / "any.pdf")
