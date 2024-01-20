"""Sharesight parser."""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Iterable, Iterator, List

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import InvalidTransactionError, ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

STOCK_ACTIVITY_COMMENT_MARKER: Final[str] = "Stock Activity"


def parse_date(val: str) -> date:
    """Parse a Sharesight report date."""

    return datetime.strptime(val, "%d/%m/%Y").date()


def parse_decimal(val: str) -> Decimal:
    """Convert value to Decimal."""
    try:
        return Decimal(val.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"Bad decimal: {val}") from None


def maybe_decimal(val: str) -> Decimal | None:
    """Convert value to Decimal."""
    return parse_decimal(val) if val else None


class SharesightTransaction(BrokerTransaction):
    """Sharesight transaction.

    Just a marker type for now
    """


class RowIterator(Iterator[List[str]]):
    """Iterator for CSV rows that keeps track of line number."""

    def __init__(self, rows: Iterable[list[str]]) -> None:
        """Initialise RowIterator."""
        self.rows = iter(rows)
        self.line = 1

    def __next__(self) -> list[str]:
        """Produce next element and increment line number."""
        elm = next(self.rows)
        self.line += 1
        return elm

    def __iter__(self) -> RowIterator:
        """Return an iterator for this object."""
        return self


def parse_dividend_payments(
    rows: Iterator[list[str]],
) -> Iterable[SharesightTransaction]:
    """Parse dividend payments from Sharesight data.

    This is the section started with a "Foreign Income" or "Local Income" header.
    We parse those two sections very similarly, so we use one function.
    """

    columns = next(rows, None)
    if columns is None:
        return

    for row in rows:
        if row[0] == "Total":
            # Don't use the totals row, but it signals the end of the section
            break

        row_dict = dict(zip(columns, row))

        dividend_date = parse_date(row_dict["Date Paid"])
        symbol = row_dict["Code"]
        symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row_dict["Comments"]
        broker = "Sharesight"

        currency = row_dict.get("Currency")
        # If we have a currency this is foreign income, otherwise it's local
        if currency:
            amount = parse_decimal(row_dict["Gross Amount"])
            tax = maybe_decimal(row_dict["Foreign Tax Deducted"])
        else:
            amount = parse_decimal(row_dict["Gross Dividend"])
            tax = maybe_decimal(row_dict["Tax Deducted"])
            # Local income must be in GBP, otherwise why are you using this tool?
            currency = "GBP"

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
                action=ActionType.TAX,
                symbol=symbol,
                description=description,
                broker=broker,
                currency=currency,
                amount=-tax,
                quantity=None,
                price=None,
                fees=Decimal(0),
            )


def parse_local_income(rows: Iterator[list[str]]) -> Iterable[SharesightTransaction]:
    """Parse Local Income section from Sharesight data.

    This basically just yields to `parse_dividend_payments`, but we skip the
    header lines
    """

    for row in rows:
        if row[0] == "Total Local Income":
            return

        if row[0] == "Dividend Payments":
            yield from parse_dividend_payments(rows)


def parse_foreign_income(rows: Iterator[list[str]]) -> Iterable[SharesightTransaction]:
    """Parse Foreign Income section from Sharesight data."""

    yield from parse_dividend_payments(rows)


def parse_income_report(file: Path) -> Iterable[SharesightTransaction]:
    """Parse the Taxable Income Report from Sharesight."""

    with file.open(encoding="utf-8") as csv_file:
        rows = list(csv.reader(csv_file))

    # Use our custom iterator for error reporting
    rows_iter = RowIterator(rows)
    for row in rows_iter:
        try:
            if row[0] == "Local Income":
                yield from parse_local_income(rows_iter)
            elif row[0] == "Foreign Income":
                yield from parse_foreign_income(rows_iter)
        except ValueError as err:
            raise ParsingError(f"{file}:{rows_iter.line}", str(err)) from None


def parse_trades(
    columns: list[str], rows: Iterator[list[str]]
) -> Iterable[SharesightTransaction]:
    """Parse content in All Trades Report from Sharesight."""

    for row in rows:
        if not any(row):
            # There is an empty row at the end of the trades list
            break

        row_dict = dict(zip(columns, row))
        tpe = row_dict["Type"]
        if tpe == "Buy":
            action = ActionType.BUY
        elif tpe == "Sell":
            action = ActionType.SELL
        else:
            raise ValueError(f"Unknown action: {tpe}")

        market = row_dict["Market"]
        symbol = f"{market}:{row_dict['Code']}"
        trade_date = parse_date(row_dict["Date"])
        quantity = parse_decimal(row_dict["Quantity"])
        price = parse_decimal(row_dict["Price *"])
        fees = maybe_decimal(row_dict["Brokerage *"]) or Decimal(0)
        currency = row_dict["Currency"]
        description = row_dict["Comments"]
        broker = "Sharesight"
        gbp_value = maybe_decimal(row_dict["Value"])

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
            columns = row
            try:
                yield from parse_trades(columns, rows_iter)
            except (InvalidOperation, ValueError) as err:
                raise ParsingError(f"{file}:{rows_iter.line}", str(err)) from None


def read_sharesight_transactions(
    transactions_folder: str,
) -> list[SharesightTransaction]:
    """Parse Sharesight transactions from reports."""

    transactions: list[SharesightTransaction] = []
    for file in Path(transactions_folder).glob("*.csv"):
        if file.match("Taxable Income Report*.csv"):
            income_transactions = list(parse_income_report(file))
            if not income_transactions:
                print(f"WARNING: no transactions detected in file {file}")
            else:
                transactions += income_transactions

        if file.match("All Trades Report*.csv"):
            trade_transactions = list(parse_trade_report(file))
            if not trade_transactions:
                print(f"WARNING: no transactions detected in file {file}")
            else:
                transactions += trade_transactions

    def key(transaction: SharesightTransaction) -> date:
        return transaction.date

    transactions.sort(key=key)
    return transactions
