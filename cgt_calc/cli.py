"""Capital Gain Calculator command line interface."""

from __future__ import annotations

import decimal
import logging
import sys
from typing import TYPE_CHECKING

from colorama import Fore

from . import render_latex
from .args_parser import (
    create_parser,
    reject_duplicate_stdin,
    reject_schwab_file_and_dir,
    resolve_reporting_period,
)
from .currency_converter import CurrencyConverter
from .current_price_fetcher import CurrentPriceFetcher
from .exceptions import CgtError
from .initial_prices import InitialPrices
from .isin_converter import IsinConverter
from .logging import setup_logging, style_text
from .parsers.broker_registry import BrokerRegistry
from .spin_off_handler import SpinOffHandler
from .transaction_dumper import dump_transactions

if TYPE_CHECKING:
    import argparse

LOGGER = logging.getLogger(__name__)


def calculate_cgt(args: argparse.Namespace) -> None:
    """Perform all the computations."""

    # Imported here because cgt_calc.main re-exports the entry points below,
    # so a module-level import either way round would be circular.
    from .main import CapitalGainsCalculator  # noqa: PLC0415

    isin_translation_file = args.isin_translation_file

    # Read data from input files
    isin_converter = IsinConverter(isin_translation_file)
    broker_transactions = BrokerRegistry.load_all_transactions(args, isin_converter)

    if (
        args.dump_transactions
        or args.dump_transactions_file
        or args.dump_transactions_columns
        or args.dump_transactions_exclude_columns
        or args.dump_transactions_maxcolwidths
    ):
        tablefmt = args.dump_transactions
        dest_path = dump_transactions(
            broker_transactions,
            tablefmt=tablefmt,
            output_file=args.dump_transactions_file,
            columns=args.dump_transactions_columns,
            exclude_columns=args.dump_transactions_exclude_columns,
            maxcolwidths=args.dump_transactions_maxcolwidths,
        )
        if dest_path is not None:
            LOGGER.info(
                style_text(
                    f"Dumped transactions saved to {dest_path}",
                    colour=Fore.GREEN,
                    emoji="📄",
                    stream=sys.stderr,
                )
            )

    currency_converter = CurrencyConverter.create(args.exchange_rates_file)
    price_fetcher = CurrentPriceFetcher(currency_converter)
    initial_prices = InitialPrices(args.initial_prices_file)
    spin_off_handler = SpinOffHandler(args.spin_offs_file)

    calculator = CapitalGainsCalculator(
        args.year,
        currency_converter,
        isin_converter,
        price_fetcher,
        spin_off_handler,
        initial_prices,
        args.interest_fund_tickers,
        cgt_exempt_tickers=args.cgt_exempt_tickers,
        balance_check=args.balance_check,
        autoconvert_currency=args.autoconvert_currency,
        calc_unrealized_gains=args.calc_unrealized_gains,
        period_start=args.period_from,
        period_end=args.period_to,
    )
    # First pass converts broker transactions to HMRC transactions,
    # collapsing all transactions with the same type within the same day.
    # It also converts prices to GBP, validates the data, plans share
    # reorganisations, and records dividends, taxes on dividends and interest
    # for the second pass to process.
    # Second pass calculates capital gain tax for the given tax year.
    report = calculator.calculate(broker_transactions)
    # The report string is newline-terminated already; avoid a trailing
    # blank line so piped output stays stable under newline normalisation.
    print(report, end="")

    # Generate PDF report.
    if not args.no_report:
        render_latex.render_pdf(
            report,
            output_path=args.output,
            skip_pdflatex=args.no_pdflatex,
        )
    done_msg = (
        "Done! Calculations complete (PDF generation skipped)."
        if args.no_pdflatex or args.no_report
        else "Done! Report generated successfully."
    )
    LOGGER.info(style_text(done_msg, colour=Fore.GREEN, emoji="🎉", stream=sys.stderr))


def main() -> int:
    """Run main function."""

    # Enable colourised logging.
    setup_logging()

    # Throw exception on accidental float usage
    decimal.getcontext().traps[decimal.FloatOperation] = True

    parser = create_parser()

    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    reject_duplicate_stdin(parser, args)
    reject_schwab_file_and_dir(parser, args)
    resolve_reporting_period(parser, args)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        calculate_cgt(args)
    except CgtError as err:
        if args.verbose:
            LOGGER.exception("Exception:")
        else:
            # Print error without traceback
            LOGGER.error("%s", err)
        return 1
    except Exception:
        # Last-resort catch for unexpected exceptions
        LOGGER.critical("Unexpected error!")
        LOGGER.exception("Details:")
        return 1

    return 0


def init() -> None:
    """Entry point."""
    sys.exit(main())


if __name__ == "__main__":
    init()
