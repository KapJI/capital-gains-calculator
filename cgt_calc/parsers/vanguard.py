"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
import logging
from pathlib import Path
import re
from typing import Final

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

COLUMNS: Final[list[str]] = [
    "Date",
    "Details",
    "Amount",
    "Balance",
]

BOUGHT_RE = re.compile(r"^Bought (\d*[,]?\d*) .*\((.*)\)$")
SOLD_RE = re.compile(r"^Sold (\d*[,]?\d*) .*\((.*)\)$")
DIV_RE = re.compile(r"^DIV: ([^\.]+)\.[^ ]+ @ ([A-Z]+) (\d*[,\.]?\d*)")
TRANSFER_RE = re.compile(
    r".*(Regular Deposit|Deposit via|Deposit for|Payment by|Account Fee).*"
)

INTEREST_STR = "Cash Account Interest"
REVERSAL_STR = "Reversal of "
LOGGER = logging.getLogger(__name__)


def action_from_str(label: str, filename: str) -> ActionType:
    """Convert label to ActionType."""

    if TRANSFER_RE.match(label):
        return ActionType.TRANSFER

    if BOUGHT_RE.match(label):
        return ActionType.BUY

    if SOLD_RE.match(label):
        return ActionType.SELL

    if DIV_RE.match(label):
        return ActionType.DIVIDEND

    if label == INTEREST_STR:
        return ActionType.INTEREST

    raise ParsingError(filename, f"Unknown action: {label}")


class VanguardTransaction(BrokerTransaction):
    """Represents a single Vanguard transaction."""

    is_reversal: bool

    def __init__(
        self,
        header: list[str],
        row_raw: list[str],
        file: str,
    ):
        """Create transaction from CSV row."""
        currency = "GBP"
        broker = "Vanguard"

        if len(row_raw) != len(COLUMNS):
            raise UnexpectedColumnCountError(row_raw, len(COLUMNS), file)

        row = dict(zip(header, row_raw, strict=True))

        date_str = row["Date"]
        date = datetime.datetime.strptime(date_str, "%d/%m/%Y").date()

        details = row["Details"]
        self.is_reversal = False
        if details.startswith(REVERSAL_STR):
            details = details[len(REVERSAL_STR) :]
            self.is_reversal = True

        action = action_from_str(details, file)

        fees = Decimal(0)
        amount = Decimal(row["Amount"].replace(",", ""))

        quantity = None
        price = None
        symbol = None
        if action == ActionType.BUY:
            match = BOUGHT_RE.match(details)
            assert match
            quantity = Decimal(match.group(1).replace(",", ""))
            symbol = match.group(2)
            price = abs(amount) / quantity
        elif action == ActionType.SELL:
            match = SOLD_RE.match(details)
            assert match
            quantity = Decimal(match.group(1).replace(",", ""))
            symbol = match.group(2)
            price = amount / quantity
        elif action == ActionType.DIVIDEND:
            match = DIV_RE.match(details)
            assert match
            symbol = match.group(1)
            currency = match.group(2)
            price = Decimal(match.group(3).replace(",", ""))
            quantity = Decimal(round(amount / price))

        super().__init__(
            date,
            action,
            symbol,
            "",
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
        )


def validate_header(header: list[str], filename: str) -> None:
    """Check if header is valid."""
    for actual in header:
        if actual not in COLUMNS:
            msg = f"Unknown column {actual}"
            raise ParsingError(filename, msg)


def by_date_and_action(
    transaction: VanguardTransaction,
) -> tuple[datetime.date, bool, bool]:
    """Sort by date and action type."""
    # Deprioritize BUY and reversal transaction to prevent balance errors
    return (
        transaction.date,
        transaction.action == ActionType.BUY,
        transaction.is_reversal,
    )


def read_vanguard_transactions(transactions_file: str) -> list[VanguardTransaction]:
    """Read Vanguard transactions from file."""
    transactions = []
    try:
        with Path(transactions_file).open(encoding="utf-8") as csv_file:
            lines = list(csv.reader(csv_file))

            header = lines[0]
            validate_header(header, transactions_file)

            lines = lines[1:]
            cur_transactions = [
                VanguardTransaction(header, row, transactions_file) for row in lines
            ]
            if len(cur_transactions) == 0:
                LOGGER.warning(
                    "No transactions detected in file: %s", transactions_file
                )
            transactions += cur_transactions

            transactions.sort(key=by_date_and_action)

    except FileNotFoundError as err:
        raise ParsingError(
            transactions_file, "Couldn't locate Vanguard transactions file"
        ) from err

    return transactions
