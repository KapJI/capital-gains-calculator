"""Parse command line arguments."""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .const import (
    DEFAULT_EXCHANGE_RATES_FILE,
    DEFAULT_ISIN_TRANSLATION_FILE,
    DEFAULT_REPORT_PATH,
    DEFAULT_SPIN_OFF_FILE,
    INTERNAL_START_DATE,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

LOGGER = logging.getLogger(__name__)


def get_last_elapsed_tax_year() -> int:
    """Get last ended tax year."""
    now = datetime.datetime.now()
    if now.date() >= datetime.date(now.year, 4, 6):
        return now.year - 1
    return now.year - 2


def year_type(value: str) -> int:
    """Validate and convert year argument."""
    try:
        year = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from err

    min_year = INTERNAL_START_DATE.year
    max_year = datetime.datetime.now().year

    if year < min_year or year > max_year:
        raise argparse.ArgumentTypeError(
            f"year must be between {min_year} and {max_year}, got {year}"
        )

    return year


def ticker_list_type(value: str) -> list[str]:
    """Split comma-separated tickers and convert to uppercase list."""
    return [ticker.strip().upper() for ticker in value.split(",") if ticker.strip()]


def output_path_type(value: str) -> Path:
    """Validate non-empty output path and convert to Path."""
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    return Path(value)


def _ensure_readable_file(path: Path, value: str) -> None:
    """Raise ArgumentTypeError when file cannot be read."""
    try:
        with path.open("rb"):
            pass
    except OSError as err:
        raise argparse.ArgumentTypeError(
            f"unable to read file path: '{value}': {err}"
        ) from err


def _ensure_readable_directory(path: Path, value: str) -> None:
    """Raise ArgumentTypeError when directory contents cannot be listed."""
    try:
        iterator = path.iterdir()
        next(iterator, None)
    except OSError as err:
        raise argparse.ArgumentTypeError(
            f"unable to read directory path: '{value}': {err}"
        ) from err


def optional_file_type(value: str) -> Path | None:
    """Convert non-empty value to Path and ensure file semantics."""
    if value.strip() == "":
        return None
    path = Path(value)
    if path.exists():
        if not path.is_file():
            raise argparse.ArgumentTypeError(
                f"expected file path, got directory: '{value}'"
            )
        _ensure_readable_file(path, value)
    return path


def _existing_path_type(value: str, *, require_dir: bool) -> Path:
    """Ensure provided path exists and matches expected type."""
    path = Path(value).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: '{value}'")
    if require_dir and not path.is_dir():
        raise argparse.ArgumentTypeError(f"expected directory path, got: '{value}'")
    if not require_dir and not path.is_file():
        raise argparse.ArgumentTypeError(f"expected file path, got: '{value}'")
    if require_dir:
        _ensure_readable_directory(path, value)
    else:
        _ensure_readable_file(path, value)
    return path


def existing_file_type(value: str) -> Path:
    """Validate that provided value points to an existing file."""
    return _existing_path_type(value, require_dir=False)


def existing_directory_type(value: str) -> Path:
    """Validate that provided value points to an existing directory."""
    return _existing_path_type(value, require_dir=True)


class DeprecatedAction(argparse.Action):
    """Print warning when deprecated argument is used."""

    def __call__(  # type: ignore[explicit-any]
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        """Check if argument is deprecated."""
        assert isinstance(option_string, str), "Positional arguments are not supported"
        replacements: dict[str, str] = {
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
    broker_group.add_argument(
        "--freetrade-file",
        type=existing_file_type,
        default=None,
        metavar="PATH",
        help="Freetrade transaction history in CSV format",
    )
    broker_group.add_argument(
        "--freetrade",
        action=DeprecatedAction,
        dest="freetrade_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--mssb-dir",
        type=existing_directory_type,
        metavar="DIR",
        help="directory with Morgan Stanley transaction history CSV files",
    )
    broker_group.add_argument(
        "--mssb",
        action=DeprecatedAction,
        dest="mssb_dir",
        type=existing_directory_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--raw-file",
        type=existing_file_type,
        metavar="PATH",
        help="RAW format transaction history in CSV format",
    )
    broker_group.add_argument(
        "--raw",
        action=DeprecatedAction,
        dest="raw_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--schwab-file",
        type=existing_file_type,
        metavar="PATH",
        help="Charles Schwab transaction history in CSV format",
    )
    broker_group.add_argument(
        "--schwab",
        action=DeprecatedAction,
        dest="schwab_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--schwab-award-file",
        type=existing_file_type,
        default=None,
        metavar="PATH",
        help="Charles Schwab Equity Awards transaction history in CSV format",
    )
    broker_group.add_argument(
        "--schwab-award",
        action=DeprecatedAction,
        dest="schwab_award_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--schwab-equity-award-json",
        type=existing_file_type,
        default=None,
        metavar="PATH",
        help="Charles Schwab Equity Awards transaction history in JSON format",
    )
    broker_group.add_argument(
        "--schwab_equity_award_json",
        action=DeprecatedAction,
        type=existing_file_type,
        default=None,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--sharesight-dir",
        type=existing_directory_type,
        metavar="DIR",
        help="directory with Sharesight reports in CSV format",
    )
    broker_group.add_argument(
        "--sharesight",
        action=DeprecatedAction,
        dest="sharesight_dir",
        type=existing_directory_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--trading212-dir",
        type=existing_directory_type,
        metavar="DIR",
        help="directory with Trading 212 transaction history CSV files",
    )
    broker_group.add_argument(
        "--trading212",
        action=DeprecatedAction,
        dest="trading212_dir",
        type=existing_directory_type,
        help=argparse.SUPPRESS,
    )
    broker_group.add_argument(
        "--vanguard-file",
        type=existing_file_type,
        metavar="PATH",
        help="Vanguard transaction history in CSV format",
    )
    broker_group.add_argument(
        "--vanguard",
        dest="vanguard_file",
        type=existing_file_type,
        help=argparse.SUPPRESS,
    )

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
        "--eri-raw-file",
        type=existing_file_type,
        metavar="PATH",
        help="historical Excess Reported Income data in CSV format",
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
