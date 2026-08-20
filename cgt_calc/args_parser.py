"""Parse command line arguments."""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import logging

import shtab

from .args_validators import (
    DeprecatedAction,
    date_type,
    existing_file_type,
    optional_file_type,
    output_path_type,
    set_completer,
    ticker_list_type,
    year_type,
)
from .const import (
    DEFAULT_EXCHANGE_RATES_FILE,
    DEFAULT_ISIN_TRANSLATION_FILE,
    DEFAULT_REPORT_PATH,
    DEFAULT_SPIN_OFF_FILE,
)
from .dates import get_tax_year_end, get_tax_year_for_date, get_tax_year_start
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
        default=None,
        help="first year of the UK tax year (e.g. 2024 for tax year 2024/25; "
        f"default: {get_last_elapsed_tax_year()})",
    )
    year_group.add_argument(
        "--from",
        dest="period_from",
        type=date_type,
        metavar="YYYY-MM-DD",
        help="start of a custom reporting period, e.g. for the HMRC 2024/25 "
        "CGT adjustment calculation (requires --to, incompatible with --year)",
    )
    year_group.add_argument(
        "--to",
        dest="period_to",
        type=date_type,
        metavar="YYYY-MM-DD",
        help="end of a custom reporting period (requires --from)",
    )

    # Broker Inputs
    broker_group = parser.add_argument_group("Broker inputs")
    BrokerRegistry.register_all_arguments(broker_group)

    # Additional Data Files
    data_group = parser.add_argument_group("Additional data files")
    set_completer(
        data_group.add_argument(
            "--initial-prices-file",
            type=existing_file_type,
            metavar="PATH",
            help="stock prices in USD at key events (vesting, splits, etc.) "
            "in CSV format",
        ),
        shtab.FILE,
    )
    data_group.add_argument(
        "--initial-prices",
        action=DeprecatedAction,
        dest="initial_prices_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    set_completer(
        data_group.add_argument(
            "--exchange-rates-file",
            type=optional_file_type,
            metavar="PATH",
            default=DEFAULT_EXCHANGE_RATES_FILE,
            help="monthly exchange rates in CSV format (generated automatically if missing; default: %(default)s)",
        ),
        shtab.FILE,
    )
    set_completer(
        data_group.add_argument(
            "--isin-translation-file",
            type=optional_file_type,
            default=DEFAULT_ISIN_TRANSLATION_FILE,
            metavar="PATH",
            help="ISIN to ticker translations in CSV format (generated automatically if missing; default: %(default)s)",
        ),
        shtab.FILE,
    )
    set_completer(
        data_group.add_argument(
            "--spin-offs-file",
            type=optional_file_type,
            metavar="PATH",
            default=DEFAULT_SPIN_OFF_FILE,
            help="spin-offs data in CSV format (default: %(default)s)",
        ),
        shtab.FILE,
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
    set_completer(
        output_mutex.add_argument(
            "-o",
            "--output",
            type=output_path_type,
            metavar="PATH",
            default=DEFAULT_REPORT_PATH,
            help="path to save the generated PDF report (default: %(default)s)",
        ),
        shtab.FILE,
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
    shtab.add_argument_to(
        general_group,  # type: ignore[arg-type]  # groups work like parsers here
        "--print-completion",
        help="print shell tab completion script and exit",
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


def resolve_reporting_period(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Validate --year/--from/--to and resolve the effective tax year.

    Sets args.year from the custom period when --from/--to are used,
    or to the last elapsed tax year when nothing is specified.
    """
    if args.period_from is None and args.period_to is None:
        if args.year is None:
            args.year = get_last_elapsed_tax_year()
        return
    if args.period_from is None or args.period_to is None:
        parser.error("--from and --to must be used together")
    if args.year is not None:
        parser.error("--year cannot be combined with --from/--to")
    if args.period_from > args.period_to:
        parser.error("--from must not be after --to")
    tax_year = get_tax_year_for_date(args.period_from)
    if tax_year != get_tax_year_for_date(args.period_to):
        parser.error(
            "--from and --to must be within the same UK tax year, "
            f"e.g. {get_tax_year_start(tax_year)} to {get_tax_year_end(tax_year)}"
        )
    args.year = tax_year
