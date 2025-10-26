"""Sharesight parser."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import (
    InvalidTransactionError,
    ParsingError,
    UnexpectedColumnCountError,
)
from cgt_calc.model import ActionType, BrokerTransaction

if TYPE_CHECKING:
    from pathlib import Path

STOCK_ACTIVITY_COMMENT_MARKER: Final[str] = "Stock Activity"
LOGGER = logging.getLogger(__name__)


class DividendColumn(StrEnum):
    """Columns available in Sharesight dividend sections."""

    CODE = "Code"
    NAME = "Name"
    DATE_PAID = "Date Paid"
    COMMENTS = "Comments"
    NET_DIVIDEND = "Net Dividend"
    TAX_DEDUCTED = "Tax Deducted"
    TAX_CREDIT = "Tax Credit"
    GROSS_DIVIDEND = "Gross Dividend"
    EXCHANGE_RATE = "Exchange Rate"
    CURRENCY = "Currency"
    NET_AMOUNT = "Net Amount"
    FOREIGN_TAX_DEDUCTED = "Foreign Tax Deducted"
    GROSS_AMOUNT = "Gross Amount"


@dataclass(frozen=True)
class DividendSectionSchema:
    """Schema describing a dividend section in the income report."""

    section_name: str
    expected_columns: tuple[DividendColumn, ...]
    amount_column: DividendColumn
    tax_column: DividendColumn
    currency_column: DividendColumn | None
    default_currency: str | None = None


LOCAL_DIVIDEND_SCHEMA = DividendSectionSchema(
    section_name="Sharesight local dividend header",
    expected_columns=(
        DividendColumn.CODE,
        DividendColumn.NAME,
        DividendColumn.DATE_PAID,
        DividendColumn.NET_DIVIDEND,
        DividendColumn.TAX_DEDUCTED,
        DividendColumn.TAX_CREDIT,
        DividendColumn.GROSS_DIVIDEND,
        DividendColumn.COMMENTS,
    ),
    amount_column=DividendColumn.GROSS_DIVIDEND,
    tax_column=DividendColumn.TAX_DEDUCTED,
    currency_column=None,
    default_currency="GBP",
)


FOREIGN_DIVIDEND_SCHEMA = DividendSectionSchema(
    section_name="Sharesight foreign dividend header",
    expected_columns=(
        DividendColumn.CODE,
        DividendColumn.NAME,
        DividendColumn.DATE_PAID,
        DividendColumn.EXCHANGE_RATE,
        DividendColumn.CURRENCY,
        DividendColumn.NET_AMOUNT,
        DividendColumn.FOREIGN_TAX_DEDUCTED,
        DividendColumn.GROSS_AMOUNT,
        DividendColumn.COMMENTS,
    ),
    amount_column=DividendColumn.GROSS_AMOUNT,
    tax_column=DividendColumn.FOREIGN_TAX_DEDUCTED,
    currency_column=DividendColumn.CURRENCY,
    default_currency=None,
)


class TradeColumn(StrEnum):
    """Columns present in the Sharesight trades report."""

    MARKET = "Market"
    CODE = "Code"
    NAME = "Name"
    TYPE = "Type"
    DATE = "Date"
    QUANTITY = "Quantity"
    PRICE = "Price *"
    BROKERAGE = "Brokerage *"
    CURRENCY = "Currency"
    EXCHANGE_RATE = "Exchange Rate"
    VALUE = "Value"
    COMMENTS = "Comments"


class SharesightTransaction(BrokerTransaction):
    """Sharesight transaction.

    Just a marker type for now
    """


class RowIterator(Iterator[list[str]]):
    """Iterator for CSV rows that keeps track of line number."""

    def __init__(self, rows: Iterable[list[str]]) -> None:
        """Initialise RowIterator."""
        self.rows = iter(rows)
        self.line = 0

    def __next__(self) -> list[str]:
        """Produce next element and increment line number."""
        elm = next(self.rows)
        self.line += 1
        return elm

    def __iter__(self) -> RowIterator:
        """Return an iterator for this object."""
        return self


def parse_date(val: str) -> date:
    """Parse a Sharesight report date."""

    return datetime.strptime(val, "%d/%m/%Y").date()


def parse_decimal[Col: StrEnum](row_dict: Mapping[Col, str], column: Col) -> Decimal:
    """Convert column value to Decimal."""

    raw_value = row_dict[column]
    try:
        return Decimal(raw_value.replace(",", ""))
    except InvalidOperation as err:
        raise ValueError(
            f"Invalid decimal in column '{column.value}': {raw_value!r}"
        ) from err


def maybe_decimal[Col: StrEnum](
    row_dict: Mapping[Col, str], column: Col
) -> Decimal | None:
    """Convert column value to Decimal if provided."""

    raw_value = row_dict.get(column, "")
    return parse_decimal(row_dict, column) if raw_value else None


def validate_header(
    header: list[str],
    expected: Iterable[StrEnum],
    *,
    file: Path,
    section: str,
) -> None:
    """Validate that all expected columns are present."""

    expected_values = {column.value for column in expected}
    header_values = {column for column in header if column}
    missing = expected_values - header_values
    if missing:
        raise ParsingError(
            file,
            f"Missing expected columns in {section}: {', '.join(sorted(missing))}",
        )


def parse_dividend_payments(
    schema: DividendSectionSchema,
    rows: Iterator[list[str]],
    file: Path,
) -> Iterable[SharesightTransaction]:
    """Parse dividend payments from Sharesight data.

    This is the section started with a "Foreign Income" or "Local Income" header.
    We parse those two sections very similarly, so we use one function.
    """

    header = next(rows, None)
    if header is None:
        return

    validate_header(
        header,
        schema.expected_columns,
        file=file,
        section=schema.section_name,
    )

    for row in rows:
        if row[0] == "Total":
            # Don't use the totals row, but it signals the end of the section
            break

        if len(row) != len(header):
            raise UnexpectedColumnCountError(row, len(header), file)

        row_dict = {
            DividendColumn(column): value
            for column, value in zip(header, row, strict=True)
            if column
        }

        dividend_date = parse_date(row_dict[DividendColumn.DATE_PAID])
        symbol = row_dict[DividendColumn.CODE]
        symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row_dict[DividendColumn.COMMENTS]
        broker = "Sharesight"

        if schema.default_currency:
            currency = schema.default_currency
        else:
            if schema.currency_column is None:
                raise ValueError(
                    f"Missing currency column definition for {schema.section_name}"
                )

            currency = row_dict.get(schema.currency_column, "").strip()
            if not currency:
                raise ValueError(
                    "Missing currency in column "
                    f"'{schema.currency_column.value}' for {schema.section_name}"
                )

        amount = parse_decimal(row_dict, schema.amount_column)
        tax = maybe_decimal(row_dict, schema.tax_column)

        yield SharesightTransaction(
            date=dividend_date,
            action=ActionType.DIVIDEND,
            symbol=symbol,
            description=description,
            broker=broker,
            currency=currency,
            amount=amount,
            quantity=None,
            price=None,
            fees=Decimal(0),
        )

        # Generate the tax as a separate transaction
        if tax:
            yield SharesightTransaction(
                date=dividend_date,
                action=ActionType.DIVIDEND_TAX,
                symbol=symbol,
                description=description,
                broker=broker,
                currency=currency,
                amount=-tax,
                quantity=None,
                price=None,
                fees=Decimal(0),
            )


def parse_local_income(
    rows: Iterator[list[str]], file: Path
) -> Iterable[SharesightTransaction]:
    """Parse Local Income section from Sharesight data.

    This basically just yields to `parse_dividend_payments`, but we skip the
    header lines
    """

    for row in rows:
        if row[0] == "Total Local Income":
            return

        if row[0] == "Dividend Payments":
            yield from parse_dividend_payments(LOCAL_DIVIDEND_SCHEMA, rows, file)


def parse_foreign_income(
    rows: Iterator[list[str]], file: Path
) -> Iterable[SharesightTransaction]:
    """Parse Foreign Income section from Sharesight data."""

    yield from parse_dividend_payments(FOREIGN_DIVIDEND_SCHEMA, rows, file)


def parse_income_report(file: Path) -> Iterable[SharesightTransaction]:
    """Parse the Taxable Income Report from Sharesight."""

    with file.open(encoding="utf-8") as csv_file:
        rows = list(csv.reader(csv_file))

    # Use our custom iterator for error reporting
    rows_iter = RowIterator(rows)
    try:
        for row in rows_iter:
            if row[0] == "Local Income":
                yield from parse_local_income(rows_iter, file)
            elif row[0] == "Foreign Income":
                yield from parse_foreign_income(rows_iter, file)
    except ParsingError as err:
        err.add_row_context(rows_iter.line)
        raise
    except ValueError as err:
        raise ParsingError(file, str(err), row_index=rows_iter.line) from err


def parse_trades(
    header: list[str], rows: Iterator[list[str]], file: Path
) -> Iterable[SharesightTransaction]:
    """Parse content in All Trades Report from Sharesight."""

    validate_header(
        header,
        TradeColumn,
        file=file,
        section="Sharesight trades header",
    )

    for row in rows:
        if not any(row):
            # There is an empty row at the end of the trades list
            break

        if len(row) != len(header):
            raise UnexpectedColumnCountError(row, len(header), file)

        row_dict = {
            TradeColumn(column): value
            for column, value in zip(header, row, strict=True)
            if column
        }

        trade_type = row_dict[TradeColumn.TYPE]
        if trade_type == "Buy":
            action = ActionType.BUY
        elif trade_type == "Sell":
            action = ActionType.SELL
        else:
            raise ValueError(f"Unknown action: {trade_type}")

        market = row_dict[TradeColumn.MARKET]
        symbol = f"{market}:{row_dict[TradeColumn.CODE]}"
        trade_date = parse_date(row_dict[TradeColumn.DATE])
        quantity = parse_decimal(row_dict, TradeColumn.QUANTITY)
        price = parse_decimal(row_dict, TradeColumn.PRICE)
        fees = maybe_decimal(row_dict, TradeColumn.BROKERAGE) or Decimal(0)
        currency = row_dict[TradeColumn.CURRENCY]
        description = row_dict[TradeColumn.COMMENTS]
        broker = "Sharesight"
        gbp_value = maybe_decimal(row_dict, TradeColumn.VALUE)

        # Sharesight's reports conventions are slightly different from our
        # conventions:
        # - Quantity is negative on sell
        # - Amount is negative when selling, and positive when buying
        #   (as it tracks portfolio value, not account balance)
        # - Value provided is always in GBP
        # - Foreign exchange transactions are show as BUY and SELL

        if market == "FX":
            # While Sharesight provides an exchange rate, it is not precise enough
            # for cryptocurrency transactions
            if not gbp_value:
                raise ValueError("Missing Value in FX transaction")

            price = abs(gbp_value / quantity)
            currency = "GBP"

        # Make amount positive on sell and negative on buy
        amount = -(quantity * price) - fees
        # Make quantity always positive
        quantity = abs(quantity)

        # Create the transaction object now so we can report it in exceptions
        transaction = SharesightTransaction(
            date=trade_date,
            action=action,
            symbol=symbol,
            description=description,
            quantity=quantity,
            price=price,
            fees=fees,
            amount=amount,
            currency=currency,
            broker=broker,
        )

        # Sharesight has no native support for stock activity, so use a string
        # in the trade comment to mark it
        if STOCK_ACTIVITY_COMMENT_MARKER.lower() in description.lower():
            # Stock activity that is not a grant is weird and unsupported
            if action != ActionType.BUY:
                raise InvalidTransactionError(
                    transaction, "Stock activity must have Type=Buy"
                )

            transaction.action = ActionType.STOCK_ACTIVITY
            transaction.amount = None

        yield transaction


def parse_trade_report(file: Path) -> Iterable[SharesightTransaction]:
    """Parse All Trades Report from Sharesight."""

    with file.open(encoding="utf-8") as csv_file:
        rows = list(csv.reader(csv_file))

    # Use our custom iterator for error reporting
    rows_iter = RowIterator(rows)
    for row in rows_iter:
        # Skip everything until we find the header
        if row[0] == "Market":
            header = row
            try:
                yield from parse_trades(header, rows_iter, file)
            except ParsingError as err:
                err.add_row_context(rows_iter.line)
                raise
            except ValueError as err:
                raise ParsingError(file, str(err), row_index=rows_iter.line) from err


def read_sharesight_transactions(
    transactions_folder: Path,
) -> list[SharesightTransaction]:
    """Parse Sharesight transactions from reports."""

    transactions: list[SharesightTransaction] = []
    for file in sorted(transactions_folder.glob("*.csv")):
        if file.match("Taxable Income Report*.csv"):
            print(f"Parsing {file}...")
            income_transactions = list(parse_income_report(file))
            if not income_transactions:
                LOGGER.warning("No transactions detected in file: %s", file)
            else:
                transactions += income_transactions

        if file.match("All Trades Report*.csv"):
            print(f"Parsing {file}...")
            trade_transactions = list(parse_trade_report(file))
            if not trade_transactions:
                LOGGER.warning("No transactions detected in file: %s", file)
            else:
                transactions += trade_transactions

    def key(transaction: SharesightTransaction) -> date:
        return transaction.date

    transactions.sort(key=key)
    return transactions
