"""Constants."""

from __future__ import annotations

import datetime
from decimal import Decimal
import os
from pathlib import Path
from typing import Final

from dateutil.relativedelta import relativedelta

from .model import TaxTreaty

# =============================================================================
# Allowances
# =============================================================================

# Capital Gains Tax annual exempt amount (tax-free allowance)
# https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances#tax-free-allowances-for-capital-gains-tax
CAPITAL_GAIN_ALLOWANCES: Final[dict[int, int]] = {
    2014: 11000,
    2015: 11100,
    2016: 11100,
    2017: 11300,
    2018: 11700,
    2019: 12000,
    2020: 12300,
    2021: 12300,
    2022: 12300,
    2023: 6000,
    2024: 3000,
    2025: 3000,
}

# Dividend Tax annual allowance
# https://www.gov.uk/tax-on-dividends
DIVIDEND_ALLOWANCES: Final[dict[int, int]] = {
    2019: 2000,
    2020: 2000,
    2021: 2000,
    2022: 2000,
    2023: 1000,
    2024: 500,
}


# =============================================================================
# Double taxation
# =============================================================================

# Country and treaty rates per country
# https://www.gov.uk/hmrc-internal-manuals/double-taxation-relief
DIVIDEND_DOUBLE_TAXATION_RULES: Final[dict[str, TaxTreaty]] = {
    "USD": TaxTreaty("USA", Decimal("0.15"), Decimal("0.15")),
    "PLN": TaxTreaty("Poland", Decimal("0.19"), Decimal("0.1")),
}


# =============================================================================
# General constants
# =============================================================================

CGT_TEST_MODE = os.environ.get("CGT_TEST_MODE", "0") == "1"
INTERNAL_START_DATE: Final = datetime.date(2010, 1, 1)
BED_AND_BREAKFAST_DAYS: Final = 30
UK_CURRENCY: Final = "GBP"
ERI_TAX_DATE_DELTA: Final = relativedelta(months=6)

TICKER_RENAMES: Final[dict[str, str]] = {
    "FB": "META",
}


# =============================================================================
# Resource files
# =============================================================================

PACKAGE_NAME: Final = __package__

# LaTeX template for calculations report
LATEX_TEMPLATE_RESOURCE: Final = "template.tex.j2"

# Initial vesting and spin-off prices
INITIAL_PRICES_RESOURCE: Final = "initial_prices.csv"

# ISIN initial translation file
INITIAL_ISIN_TRANSLATION_RESOURCE: Final = "initial_isin_translation.csv"

# ERI data folder
ERI_RESOURCE_FOLDER: Final = "eri"


# =============================================================================
# Default output paths
# =============================================================================

DEFAULT_OUTPUT_FOLDER: Final = Path("out")

# Generated PDF report
DEFAULT_REPORT_PATH: Final = DEFAULT_OUTPUT_FOLDER / "calculations.pdf"

# Monthly exchange rates from HMRC
DEFAULT_EXCHANGE_RATES_FILE: Final = DEFAULT_OUTPUT_FOLDER / "exchange_rates.csv"

# Spin-offs output file
DEFAULT_SPIN_OFF_FILE: Final = DEFAULT_OUTPUT_FOLDER / "spin_offs.csv"

# ISIN to ticker translation file
DEFAULT_ISIN_TRANSLATION_FILE: Final = DEFAULT_OUTPUT_FOLDER / "isin_translation.csv"
