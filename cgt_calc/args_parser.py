"""Parse command line arguments."""
import argparse
import datetime

from .const import DEFAULT_REPORT_PATH


def get_last_elapsed_tax_year() -> int:
    """Get last ended tax year."""
    now = datetime.datetime.now()
    if now.date() >= datetime.date(now.year, 4, 6):
        return now.year - 1
    return now.year - 2


def create_parser() -> argparse.ArgumentParser:
    """Create ArgumentParser."""
    parser = argparse.ArgumentParser(
        description="Calculate capital gains from stock transactions.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=get_last_elapsed_tax_year(),
        nargs="?",
        help="First year of the tax year to calculate gains on (default: %(default)d)",
    )
    parser.add_argument(
        "--schwab",
        type=str,
        nargs="?",
        help="file containing the exported transactions from Charles Schwab",
    )
    parser.add_argument(
        "--trading212",
        type=str,
        nargs="?",
        help="folder containing the exported transaction files from Trading 212",
    )
    parser.add_argument(
        "--gbp_history",
        type=str,
        default=None,
        nargs="?",
        help="monthly GBP/USD prices from HMRC",
    )
    parser.add_argument(
        "--initial_prices",
        type=str,
        default=None,
        nargs="?",
        help="file containing stock prices in USD at the moment of vesting, split, etc",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=DEFAULT_REPORT_PATH,
        nargs="?",
        help="where to save the generated pdf report (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable extra logging",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print version",
    )
    return parser
