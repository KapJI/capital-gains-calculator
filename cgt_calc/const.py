"""Constants."""

from __future__ import annotations

import datetime
from decimal import Decimal
import os
from typing import Final

from dateutil.relativedelta import relativedelta

from .model import TaxTreaty

CGT_TEST_MODE = os.environ.get("CGT_TEST_MODE", "0") == "1"

# Allowances from
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
}

# Allowances from
# https://www.gov.uk/tax-on-dividends
DIVIDEND_ALLOWANCES: Final[dict[int, int]] = {
    2019: 2000,
    2020: 2000,
    2021: 2000,
    2022: 2000,
    2023: 1000,
    2024: 500,
}

# Rules from
# https://www.gov.uk/hmrc-internal-manuals/double-taxation-relief
DIVIDEND_DOUBLE_TAXATION_RULES = {
    "USD": TaxTreaty("USA", Decimal(0.15), Decimal(0.15)),
    "PLN": TaxTreaty("Poland", Decimal(0.19), Decimal(0.1)),
}


DEFAULT_REPORT_PATH: Final = "calculations.pdf"

INTERNAL_START_DATE: Final = datetime.date(2010, 1, 1)

# Resources

PACKAGE_NAME = __package__

# Monthly exchange rate history from HMRC
DEFAULT_EXCHANGE_RATES_FILE: Final = "exchange_rates.csv"

# Initial vesting and spin-off prices
DEFAULT_INITIAL_PRICES_FILE: Final = "initial_prices.csv"

DEFAULT_SPIN_OFF_FILE: Final = "spin_offs.csv"

DEFAULT_ERI_FOLDER: Final = "eri"

# Latex template for calculations report
TEMPLATE_NAME: Final = "template.tex.j2"

BED_AND_BREAKFAST_DAYS: Final = 30

TICKER_RENAMES: Final[dict[str, str]] = {
    "FB": "META",
}

COUNTRY_CURRENCY = "GBP"

ERI_TAX_DATE_DELTA: Final = relativedelta(months=6)

# ISIN to ticker translation file
DEFAULT_ISIN_TRANSLATION_FILE: Final = "isin_translation.csv"

# ISIN initial translation file
INITIAL_ISIN_TRANSLATION_FILE: Final = "initial_isin_translation.csv"
