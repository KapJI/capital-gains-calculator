import argparse
import datetime


def get_last_elapsed_tax_year() -> int:
    now = datetime.datetime.now()
    if now.date() >= datetime.date(now.year, 4, 6):
        return now.year - 1
    else:
        return now.year - 2


def create_parser() -> argparse.ArgumentParser:
    # Schwab transactions
    # Monthly GBP/USD history from
    # https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat
    default_gbp_history_file = "cgt_calc/resources/GBP_USD_monthly_history.csv"
    # Initial vesting and spin-off prices
    default_initial_prices_file = "cgt_calc/resources/initial_prices.csv"
    default_pdf_report = "calculations.pdf"

    parser = argparse.ArgumentParser(
        description="Calculate capital gains from stock transactions.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--tax_year",
        type=int,
        default=get_last_elapsed_tax_year(),
        nargs="?",
        help="First year of the tax year to calculate gains on",
    )
    parser.add_argument(
        "--schwab",
        type=str,
        nargs="?",
        help="file containing the exported transactions from Schwab",
    )
    parser.add_argument(
        "--trading212",
        type=str,
        nargs="?",
        help="folder containing the exported transaction files from Trading212",
    )
    parser.add_argument(
        "--gbp_history",
        type=str,
        default=default_gbp_history_file,
        nargs="?",
        help="monthly GBP/USD prices from HMRC",
    )
    parser.add_argument(
        "--initial_prices",
        type=str,
        default=default_initial_prices_file,
        nargs="?",
        help="file containing stock prices in USD at the moment of vesting, split, etc.",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=default_pdf_report,
        nargs="?",
        help="where to save the generated pdf report",
    )
    return parser
