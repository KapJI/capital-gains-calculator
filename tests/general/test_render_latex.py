"""Tests for the LaTeX report layout."""

import datetime
from decimal import Decimal
import os
from pathlib import Path
import re

import pytest

from cgt_calc.model import (
    CalculationEntry,
    CapitalGainsReport,
    ExcessReportedIncome,
    RuleType,
)
from cgt_calc.render_latex import render_pdf


def report() -> CapitalGainsReport:
    """Build a compact report that covers the summary and yield event types."""
    event_date = datetime.date(2024, 6, 30)
    interest_eri = ExcessReportedIncome(
        price=Decimal("0.3000"),
        symbol="FUND",
        date=event_date,
        distribution_date=event_date,
        is_interest=True,
    )
    calculation_log_yields = {
        event_date: {
            "interestUSD$schwab": [
                CalculationEntry(
                    RuleType.INTEREST,
                    quantity=Decimal(1),
                    amount=Decimal("2.00"),
                    fees=Decimal(0),
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                )
            ],
            "interestTaxUSD$schwab": [
                CalculationEntry(
                    RuleType.INTEREST_TAX,
                    quantity=Decimal(1),
                    amount=Decimal("0.50"),
                    fees=Decimal(0),
                    new_quantity=Decimal(1),
                    new_pool_cost=Decimal(0),
                )
            ],
            "excess-reported-income-distribution$FUND": [
                CalculationEntry(
                    RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                    quantity=Decimal(10),
                    amount=Decimal("3.00"),
                    fees=Decimal(0),
                    new_quantity=Decimal(10),
                    new_pool_cost=Decimal(0),
                    eris=[interest_eri],
                )
            ],
        }
    }
    return CapitalGainsReport(
        tax_year=2024,
        portfolio=[],
        disposal_count=2,
        disposal_proceeds=Decimal("1000.00"),
        allowable_costs=Decimal("600.00"),
        capital_gain=Decimal("450.00"),
        capital_loss=Decimal("-50.00"),
        capital_gain_allowance=Decimal("300.00"),
        dividend_allowance=Decimal("500.00"),
        calculation_log={},
        calculation_log_yields=calculation_log_yields,
        total_uk_interest=Decimal("1.00"),
        total_foreign_interest=Decimal("5.00"),
        total_interest_tax=Decimal("0.50"),
        show_unrealized_gains=False,
        gift_loss=Decimal("-25.00"),
    )


def test_summary_is_first_and_uses_report_totals(tmp_path: Path) -> None:
    """Keep the PDF summary prominent and consistent with the report model."""
    output_path = tmp_path / "tax-report.pdf"

    render_pdf(report(), output_path, skip_pdflatex=True)

    latex = output_path.with_suffix(".tex").read_text()
    flattened_latex = " ".join(latex.split())
    assert latex.index(r"\reportsection{Tax summary}") < latex.index(
        r"\reportsection{Capital gains events}"
    )
    assert r"\summaryrow{Allowable costs}{£600.00}" in latex
    assert r"\summaryrow{Losses on gifts (kept separate)}{£25.00}" in latex
    assert r"\summarytotal{Taxable gain}{£100.00}" in latex
    assert r"\summarynote{of which excess reported income}{£3.00}" in latex
    assert "schwab (USD), £2.00" in flattened_latex
    assert "schwab (USD), £0.50" in flattened_latex
    assert r"\IfFileExists{sourcesanspro.sty}" in latex
    assert r"\renewcommand{\familydefault}{\sfdefault}" in latex
    assert r"\definecolor{ReportNavy}{RGB}{31, 56, 77}" in latex
    assert r"\fancyhead[L]{\small\color{ReportNavy}Capital Gains Tax Report}" in latex
    packages = set(re.findall(r"\\usepackage(?:\[[^]]*\])?\{([^}]+)\}", latex))
    assert packages - {"sourcesanspro"} == {
        "array",
        "color",
        "fancyhdr",
        "geometry",
        "inputenc",
        "tabularx",
    }
    assert "xcolor" not in packages
    assert "Overall - Capital Gains" not in latex


@pytest.mark.skipif(
    not os.getenv("ENABLE_PDFLATEX"),
    reason="pdflatex is exercised by the Docker test job",
)
def test_report_compiles_with_documented_latex_install(tmp_path: Path) -> None:
    """Compile the report where CI installs only texlive-latex-base."""
    output_path = tmp_path / "tax-report.pdf"

    render_pdf(report(), output_path)

    assert output_path.is_file()
    assert output_path.stat().st_size > 0
