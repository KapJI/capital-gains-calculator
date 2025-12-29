"""Parse command line arguments."""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import logging

from .args_validators import (
    DeprecatedAction,
    existing_file_type,
    optional_file_type,
    output_path_type,
    ticker_list_type,
    year_type,
)
from .const import (
    DEFAULT_EXCHANGE_RATES_FILE,
    DEFAULT_ISIN_TRANSLATION_FILE,
    DEFAULT_REPORT_PATH,
    DEFAULT_SPIN_OFF_FILE,
)
from .parsers.broker_registry import BrokerRegistry

LOGGER = logging.getLogger(__name__)


def get_last_elapsed_tax_year() -> int:
    """Get last ended tax year."""
    now = datetime.datetime.now()
    if now.date() >= datetime.date(now.year, 4, 6):
        return now.year - 1
    return now.year - 2


def create_parser() -> argparse.ArgumentParser:
    """Create ArgumentParser."""
    parser = argparse.ArgumentParser(
        description="Calculate UK capital gains from broker transactions and generate a PDF report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        allow_abbrev=False,
        epilog="""
Environment variables:
  NO_COLOR              disable colored output
  FORCE_COLOR           force colored output
""",
    )

    # Tax Year
    year_group = parser.add_argument_group("Tax year")
    year_group.add_argument(
        "--year",
        type=year_type,
        metavar="YYYY",
        default=get_last_elapsed_tax_year(),
        help="first year of the UK tax year (e.g. 2024 for tax year 2024/25; default: %(default)d)",
    )

    # Broker Inputs
    broker_group = parser.add_argument_group("Broker inputs")
    BrokerRegistry.register_all_arguments(broker_group)

    # Additional Data Files
    data_group = parser.add_argument_group("Additional data files")
    data_group.add_argument(
        "--initial-prices-file",
        type=existing_file_type,
        metavar="PATH",
        help="stock prices in USD at key events (vesting, splits, etc.) in CSV format",
    )
    data_group.add_argument(
        "--initial-prices",
        dest="initial_prices_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    data_group.add_argument(
        "--exchange-rates-file",
        type=optional_file_type,
        metavar="PATH",
        default=DEFAULT_EXCHANGE_RATES_FILE,
        help="monthly exchange rates in CSV format (generated automatically if missing; default: %(default)s)",
    )
    data_group.add_argument(
        "--isin-translation-file",
        type=optional_file_type,
        default=DEFAULT_ISIN_TRANSLATION_FILE,
        metavar="PATH",
        help="ISIN to ticker translations in CSV format (generated automatically if missing; default: %(default)s)",
    )
    data_group.add_argument(
        "--spin-offs-file",
        type=optional_file_type,
        metavar="PATH",
        default=DEFAULT_SPIN_OFF_FILE,
        help="spin-offs data in CSV format (default: %(default)s)",
    )

    # Calculation Options
    calc_group = parser.add_argument_group("Calculation options")
    calc_group.add_argument(
        "--no-balance-check",
        dest="balance_check",
        action="store_false",
        default=True,
        help="skip balance verification (useful for partial transaction records)",
    )
    calc_group.add_argument(
        "--unrealized-gains",
        dest="calc_unrealized_gains",
        action="store_true",
        default=False,
        help="estimate unrealized gains/losses for current holdings if sold today (under Section 104 rule)",
    )
    calc_group.add_argument(
        "--interest-fund-tickers",
        type=ticker_list_type,
        metavar="TICKER[,TICKER...]",
        default=[],
        help="tickers of bond funds/ETFs whose dividends are taxed as interest in the UK",
    )

    # Output Options
    output_group = parser.add_argument_group("Output")

    output_mutex = output_group.add_mutually_exclusive_group()
    output_mutex.add_argument(
        "-o",
        "--output",
        type=output_path_type,
        metavar="PATH",
        default=DEFAULT_REPORT_PATH,
        help="path to save the generated PDF report (default: %(default)s)",
    )
    output_mutex.add_argument(
        "--report",
        action=DeprecatedAction,
        dest="output",
        type=output_path_type,
        default=DEFAULT_REPORT_PATH,
        help=argparse.SUPPRESS,
    )
    output_mutex.add_argument(
        "--no-report",
        action="store_true",
        help="do not generate PDF report",
    )

    # General Options
    general_group = parser.add_argument_group("General")
    general_group.add_argument(
        "-h",
        "--help",
        action="help",
        help="show this help message and exit",
    )
    general_group.add_argument(
        "--version",
        action="version",
        version=f"cgt-calc {importlib.metadata.version(__package__)}",
        help="show version and exit",
    )
    general_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable extra logging",
    )
    # For testing only
    general_group.add_argument(
        "--no-pdflatex",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser
