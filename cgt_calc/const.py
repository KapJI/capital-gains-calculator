"""Constants."""
from __future__ import annotations

import datetime
from typing import Final

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
}

DEFAULT_REPORT_PATH: Final = "calculations.pdf"

INTERNAL_START_DATE: Final = datetime.date(2010, 1, 1)

# Resources

PACKAGE_NAME = __package__

# Schwab transactions
# Monthly GBP/USD history from
# https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat
DEFAULT_GBP_HISTORY_FILE: Final = "GBP_USD_monthly_history.csv"

# Initial vesting and spin-off prices
DEFAULT_INITIAL_PRICES_FILE: Final = "initial_prices.csv"

# Latex template for calculations report
TEMPLATE_NAME: Final = "template.tex.j2"

BED_AND_BREAKFAST_DAYS: Final = 30
