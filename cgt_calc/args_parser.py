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
        default=get_last_elapsed_tax_year(),
        help="first year of the tax year to calculate gains on (default: %(default)d)",
    )
    parser.add_argument(
        "--freetrade-file",
        type=str,
        default=None,
        help="file containing the exported transactions from Freetrade in CSV format",
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
        help="file containing the exported transactions in a raw format csv format",
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
        help="file containing the exported transactions from Charles Schwab",
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
        help="file containing schwab award data for stock prices",
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
        help="file containing schwab equity award transactions data in JSON format",
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
        help="folder containing the exported transaction files from Trading 212",
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
        help="folder containing the exported transaction files from Morgan Stanley",
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
        help="folder containing reports from Sharesight in CSV format",
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
        help="file containing the exported transactions from Vanguard in CSV format",
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
        help="file containing the historical funds Excess Reported Income "
        "in a eri_raw CSV format",
    )

    parser.add_argument(
        "--exchange-rates-file",
        type=str,
        default=DEFAULT_EXCHANGE_RATES_FILE,
        help="output file for monthly exchange rates from HMRC (default: %(default)s)",
    )
    parser.add_argument(
        "--spin-offs-file",
        type=str,
        default=DEFAULT_SPIN_OFF_FILE,
        help="output file for spin offs data (default: %(default)s)",
    )
    parser.add_argument(
        "--initial-prices-file",
        type=str,
        default=None,
        help="file containing stock prices in USD at the moment of vesting, split, etc",
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
    )
    parser.add_argument(
        "--unrealized-gains",
        dest="calc_unrealized_gains",
        action="store_true",
        default=False,
        help=(
            "show an estimation of the gains/loss you would incur"
            " if you were to sell your holdings, under the standard 104 rule."
        ),
    )
    parser.add_argument(
        "--interest-fund-tickers",
        action=SplitArgs,
        default="",
        help=(
            "list of funds/ETF tickers in your portfolio that contains bonds "
            "and whose dividends in UK have to be taxed as interest"
        ),
    )
    parser.add_argument(
        "--isin-translation-file",
        type=str,
        default=DEFAULT_ISIN_TRANSLATION_FILE,
        help="output file for ISIN to ticker translations (default: %(default)s)",
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
        default=DEFAULT_REPORT_PATH,
        help="where to save the generated PDF report (default: %(default)s)",
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
