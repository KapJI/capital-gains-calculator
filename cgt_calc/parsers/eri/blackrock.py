"""Blackrock/iShares ERI transaction parser."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import TYPE_CHECKING

import dateutil.parser as date_parser
import pandas as pd

from cgt_calc.exceptions import ParsingError
from cgt_calc.util import is_currency, is_isin, round_decimal

if TYPE_CHECKING:
    from pathlib import Path

from .model import EriParser, EriParserOutput, EriTransaction

BLACKROCK_NAME_REGEX = re.compile(r"^.*\b(blackrock|bsf).*\.xls(x)?$")
ISHARES_NAME_REGEX = re.compile(r"^.*\b(ishares).*\.xls(x)?$")

PERIOD_RE = re.compile(r"^.* (to|\-) ")

ISIN_COLUMN = "ISIN"
REPORTING_PERIOD_COLUMN = "Reporting Period"
CURRENCY_COLUMN = "Currency"
ERI_COLUMN = "Excess of Reported Income per Unit"
COLUMNS = [ISIN_COLUMN, REPORTING_PERIOD_COLUMN, CURRENCY_COLUMN, ERI_COLUMN]

BLACKROCK_ERI_FILENAME = "blackrock_eri.csv"
ISHARES_ERI_FILENAME = "ishares_eri.csv"


class BlackrockParser(EriParser):
    """Parser for Blackrock/iShares ERI spreadsheets."""

    def __init__(self) -> None:
        """Create a new Blackrock Parser instance."""
        super().__init__(name="Blackrock/iShares")

    def parse(self, file: Path) -> EriParserOutput | None:
        """Parse a Blackrock/iShares ERI file."""
        if BLACKROCK_NAME_REGEX.match(file.name):
            result = EriParserOutput(
                transactions=[], output_file_name=BLACKROCK_ERI_FILENAME
            )
        elif ISHARES_NAME_REGEX.match(file.name):
            result = EriParserOutput(
                transactions=[], output_file_name=ISHARES_ERI_FILENAME
            )
        else:
            return None

        sheet = 0

        df = pd.read_excel(file, sheet)
        df.columns = df.columns.str.strip()

        # main header row might be the first one or after some rows
        # some reports have an additional table at the end for retired
        # funds and we want to skip it

        # Search for the header row
        header_loc = df[df == "ISIN"].dropna(axis=1, how="all").dropna(how="all")

        # first row is the header row
        if "ISIN" in df.columns.values:
            max_allowed_isin_columns = 1
            skiprows = 0
        else:
            if len(header_loc.index.values) == 0:
                raise ParsingError(file.name, "No ISIN column found!")
            max_allowed_isin_columns = 2
            # skip to the header row
            skiprows = header_loc.index.values[0] + 1

        if len(header_loc.index.values) > max_allowed_isin_columns:
            raise ParsingError(file.name, "Too many ISIN columns found!")

        nrows = None
        if len(header_loc.index.values) == max_allowed_isin_columns:
            # filter out final table
            nrows = header_loc.index.values[max_allowed_isin_columns - 1] - skiprows

        df = pd.read_excel(file, sheet, skiprows=skiprows, nrows=nrows)
        df.columns = df.columns.str.strip()
        df.columns = df.columns.str.replace("\n", " ")
        df.columns = df.columns.str.replace("  ", " ")

        df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
        cleaned_df = df.filter(regex="|".join(COLUMNS)).dropna()
        if not len(cleaned_df.columns) == len(COLUMNS):
            raise ParsingError(
                file.name,
                f"Cannot process ERI columns {cleaned_df.columns.values}, "
                f"all columns {df.columns.values}",
            )

        # Clean up all the column names
        for raw_column_name in cleaned_df.columns.values:
            for column_name in COLUMNS:
                if column_name in raw_column_name:
                    cleaned_df.rename(
                        columns={raw_column_name: column_name}, inplace=True
                    )

        for _, row in cleaned_df.iterrows():
            isin = row[ISIN_COLUMN]
            if not isinstance(isin, str) or not is_isin(isin.upper()):
                raise ParsingError(file.name, f"Not valid ISIN {isin}")
            isin = isin.upper()

            currency = row[CURRENCY_COLUMN]
            if not isinstance(currency, str):
                raise ParsingError(file.name, f"Not valid Currency {currency}")
            # Bugs in the currency codes
            currency = currency.replace("JPN", "JPY")
            if not isinstance(currency, str) or not is_currency(currency):
                raise ParsingError(file.name, f"Not valid Currency {currency}")
            currency = currency.upper()

            reporting_date_str = row[REPORTING_PERIOD_COLUMN]
            try:
                reporting_date = date_parser.parse(
                    PERIOD_RE.sub("", reporting_date_str)
                ).date()
            except Exception as e:
                raise ParsingError(
                    file.name, f"Not valid Reporting period {reporting_date_str}"
                ) from e

            amount_raw = row[ERI_COLUMN]
            try:
                amount = (
                    Decimal(0)
                    if isinstance(amount_raw, str) and amount_raw.lower() == "nil"
                    else round_decimal(Decimal.from_float(amount_raw), 5)
                )
            except Exception as e:
                raise ParsingError(
                    file.name, f"Not valid ERI amount {amount_raw}"
                ) from e

            result.transactions.append(
                EriTransaction(
                    isin=isin, date=reporting_date, price=amount, currency=currency
                )
            )

        return result
