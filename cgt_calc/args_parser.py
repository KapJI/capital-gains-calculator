"""Parse command line arguments."""

from __future__ import annotations

import argparse
import datetime
import logging

from .const import (
    DEFAULT_EXCHANGE_RATES_FILE,
    DEFAULT_ISIN_TRANSLATION_FILE,
    DEFAULT_REPORT_PATH,
    DEFAULT_SPIN_OFF_FILE,
)

LOGGER = logging.getLogger(__name__)


def get_last_elapsed_tax_year() -> int:
    """Get last ended tax year."""
    now = datetime.datetime.now()
    if now.date() >= datetime.date(now.year, 4, 6):
        return now.year - 1
    return now.year - 2


class DeprecatedAction(argparse.Action):
    """Print warning when deprecated argument is used."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,  # type: ignore[override]
        option_string: str | None = None,
    ) -> None:
        """Check if argument is deprecated."""
        assert isinstance(option_string, str), "Positional arguments are not supported"
        replacements = {
            "--freetrade": "--freetrade-file",
            "--initial-prices": "--initial-prices-file",
            "--mssb": "--mssb-dir",
            "--raw": "--raw-file",
            "--report": "--output",
            "--schwab": "--schwab-file",
            "--schwab-award": "--schwab-award-file",
            "--schwab_equity_award_json": "--schwab-equity-award-json",
            "--sharesight": "--sharesight-dir",
            "--trading212": "--trading212-dir",
            "--vanguard": "--vanguard-file",
        }
        LOGGER.warning(
            "Option '%s' is deprecated; use '%s' instead.",
            option_string,
            replacements[option_string],
        )
        setattr(namespace, self.dest, values)


class SplitArgs(argparse.Action):
    """Split arguments by comma then trim and set upper case."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,  # type: ignore[override]
        option_string: str | None = None,
    ) -> None:
        """Create a new SplitArgs."""
        setattr(
            namespace, self.dest, [value.strip().upper() for value in values.split(",")]
        )


def create_parser() -> argparse.ArgumentParser:
    """Create ArgumentParser."""
    parser = argparse.ArgumentParser(
        description="Calculate UK capital gains from broker transactions and generate a PDF report.",
    )
    parser.add_argument(
        "--year",
        type=int,
        metavar="YYYY",
        default=get_last_elapsed_tax_year(),
        help="first year of the UK tax year (e.g. 2024 for tax year 2024/25; default: %(default)d)",
    )
    parser.add_argument(
        "--freetrade-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Freetrade transaction history in CSV format",
    )
    parser.add_argument(
        "--freetrade",
        action=DeprecatedAction,
        dest="freetrade_file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--raw-file",
        type=str,
        metavar="PATH",
        help="RAW format transaction history in CSV format",
    )
    parser.add_argument(
        "--raw",
        action=DeprecatedAction,
        dest="raw_file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--schwab-file",
        type=str,
        metavar="PATH",
        help="Charles Schwab transaction history in CSV format",
    )
    parser.add_argument(
        "--schwab",
        action=DeprecatedAction,
        dest="schwab_file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--schwab-award-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Charles Schwab Equity Awards transaction history in CSV format",
    )
    parser.add_argument(
        "--schwab-award",
        action=DeprecatedAction,
        dest="schwab_award_file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--schwab-equity-award-json",
        type=str,
        default=None,
        metavar="PATH",
        help="Charles Schwab Equity Awards transaction history in JSON format",
    )
    parser.add_argument(
        "--schwab_equity_award_json",
        action=DeprecatedAction,
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--trading212-dir",
        type=str,
        metavar="DIR",
        help="directory with Trading 212 transaction history CSV files",
    )
    parser.add_argument(
        "--trading212",
        action=DeprecatedAction,
        dest="trading212_dir",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--mssb-dir",
        type=str,
        metavar="DIR",
        help="directory with Morgan Stanley transaction history CSV files",
    )
    parser.add_argument(
        "--mssb",
        action=DeprecatedAction,
        dest="mssb_dir",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--sharesight-dir",
        type=str,
        metavar="DIR",
        help="directory with Sharesight reports in CSV format",
    )
    parser.add_argument(
        "--sharesight",
        action=DeprecatedAction,
        dest="sharesight_dir",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--vanguard-file",
        type=str,
        metavar="PATH",
        help="Vanguard transaction history in CSV format",
    )
    parser.add_argument(
        "--vanguard",
        dest="vanguard_file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--eri-raw-file",
        type=str,
        metavar="PATH",
        help="historical Excess Reported Income data in CSV format",
    )

    parser.add_argument(
        "--exchange-rates-file",
        type=str,
        metavar="PATH",
        default=DEFAULT_EXCHANGE_RATES_FILE,
        help="monthly exchange rates in CSV format (generated automatically if missing; default: %(default)s)",
    )
    parser.add_argument(
        "--spin-offs-file",
        type=str,
        metavar="PATH",
        default=DEFAULT_SPIN_OFF_FILE,
        help="spin-offs data in CSV format (default: %(default)s)",
    )
    parser.add_argument(
        "--initial-prices-file",
        type=str,
        default=None,
        metavar="PATH",
        help="stock prices in USD at key events (vesting, splits, etc.) in CSV format",
    )
    parser.add_argument(
        "--initial-prices",
        dest="initial_prices_file",
        type=str,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-balance-check",
        dest="balance_check",
        action="store_false",
        default=True,
        help="skip balance verification (useful for partial transaction records)",
    )
    parser.add_argument(
        "--unrealized-gains",
        dest="calc_unrealized_gains",
        action="store_true",
        default=False,
        help="estimate unrealized gains/losses for current holdings if sold today (under Section 104 rule)",
    )
    parser.add_argument(
        "--interest-fund-tickers",
        action=SplitArgs,
        metavar="TICKER[,TICKER...]",
        default="",
        help="tickers of bond funds/ETFs whose dividends are taxed as interest in the UK",
    )
    parser.add_argument(
        "--isin-translation-file",
        type=str,
        default=DEFAULT_ISIN_TRANSLATION_FILE,
        metavar="PATH",
        help="ISIN to ticker translations in CSV format (generated automatically if missing; default: %(default)s)",
    )
    # New inputs should be above
    parser.add_argument(
        "--report",
        action=DeprecatedAction,
        dest="output",
        type=str,
        default=DEFAULT_REPORT_PATH,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        metavar="PATH",
        default=DEFAULT_REPORT_PATH,
        help="path to save the generated PDF report (default: %(default)s)",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="do not generate PDF report",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable extra logging",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print version",
    )
    # For testing only
    parser.add_argument(
        "--no-pdflatex",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser
