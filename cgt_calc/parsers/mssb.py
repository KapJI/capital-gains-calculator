"""Morgan Stanley parser.

Note, that I only had access to an Alphabet export. I have no idea how it looks like
for another company, or for a full profile.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, TextIO

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

from .base_parsers import BaseDirParser, StandardCSVParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

BROKER_NAME: Final = "Morgan Stanley"
WITHDRAWALS_REPORT_FILENAME: Final = "Withdrawals Report.csv"
RELEASES_REPORT_FILENAME: Final = "Releases Report.csv"


class ReleaseColumn(StrEnum):
    """Column names for release reports."""

    VEST_DATE = "Vest Date"
    ORDER_NUMBER = "Order Number"
    PLAN = "Plan"
    TYPE = "Type"
    STATUS = "Status"
    PRICE = "Price"
    QUANTITY = "Quantity"
    NET_CASH_PROCEEDS = "Net Cash Proceeds"
    NET_SHARE_PROCEEDS = "Net Share Proceeds"
    TAX_PAYMENT_METHOD = "Tax Payment Method"


class WithdrawalColumn(StrEnum):
    """Column names for withdrawal reports."""

    EXECUTION_DATE = "Execution Date"
    ORDER_NUMBER = "Order Number"
    PLAN = "Plan"
    TYPE = "Type"
    ORDER_STATUS = "Order Status"
    PRICE = "Price"
    QUANTITY = "Quantity"
    NET_AMOUNT = "Net Amount"
    NET_SHARE_PROCEEDS = "Net Share Proceeds"
    TAX_PAYMENT_METHOD = "Tax Payment Method"


COLUMNS_RELEASE: Final[list[str]] = [column.value for column in ReleaseColumn]
COLUMNS_WITHDRAWAL: Final[list[str]] = [column.value for column in WithdrawalColumn]

# These can be potentially wired through as a flag
KNOWN_SYMBOL_DICT: Final[dict[str, str]] = {
    "GSU Class C": "GOOG",
    "Cash": "USD",
}


@dataclass
class StockSplit:
    """Info about stock split."""

    symbol: str
    date: datetime.date
    factor: int


STOCK_SPLIT_INFO = [
    StockSplit(symbol="GOOG", date=datetime.datetime(2022, 6, 15).date(), factor=20),
]


def _parse_decimal(row: dict[str, str], column: StrEnum) -> Decimal:
    """Parse a decimal from the given row, annotating errors with column context."""

    value = row[column]
    cleaned = value.replace(",", "").replace("$", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as err:
        raise ValueError(
            f"Invalid decimal in column '{column.value}': {value!r}"
        ) from err


class MSSBParser(StandardCSVParser, BaseDirParser):
    """Morgan Stanley parser."""

    arg_name = "mssb"
    pretty_name = BROKER_NAME
    format_name = "CSV"
    glob_dir = "*.csv"
    deprecated_flags: ClassVar[list[str]] = ["--mssb"]

    @classmethod
    def file_path_filter(cls, file_path: Path) -> bool:
        """Choose which files to parse."""
        return file_path.name in [
            WITHDRAWALS_REPORT_FILENAME,
            RELEASES_REPORT_FILENAME,
        ]

    @classmethod
    def pre_reading(cls, file: TextIO, file_path: Path) -> Iterable[str]:
        """Do any preprocessing of the file before parsing the csv."""
        if file_path.name == WITHDRAWALS_REPORT_FILENAME:
            cls.columns = set(COLUMNS_WITHDRAWAL)
        else:
            cls.columns = set(COLUMNS_RELEASE)
        return file

    @classmethod
    def read_row(cls, row: dict[str, str], file_path: Path) -> BrokerTransaction | None:
        """Read a single transaction from a row in the CSV."""
        if file_path.name == WITHDRAWALS_REPORT_FILENAME:
            return cls._init_from_withdrawal_report(row, file_path)
        return cls._init_from_release_report(row, file_path)

    @staticmethod
    def _init_from_release_report(row: dict[str, str], file: Path) -> BrokerTransaction:
        plan = row[ReleaseColumn.PLAN]

        if row[ReleaseColumn.TYPE] != "Release":
            raise ParsingError(file, f"Unknown type: {row[ReleaseColumn.TYPE]}")

        if row[ReleaseColumn.STATUS] not in {"Complete", "Staged"}:
            raise ParsingError(file, f"Unknown status: {row[ReleaseColumn.STATUS]}")

        price_raw = row[ReleaseColumn.PRICE]
        if not price_raw or not price_raw.startswith("$"):
            raise ParsingError(file, f"Unknown price currency: {price_raw}")

        if row[ReleaseColumn.NET_CASH_PROCEEDS] != "$0.00":
            raise ParsingError(
                file,
                f"Non-zero Net Cash Proceeds: {row[ReleaseColumn.NET_CASH_PROCEEDS]}",
            )

        if plan not in KNOWN_SYMBOL_DICT:
            raise ParsingError(file, f"Unknown plan: {plan}")

        quantity = _parse_decimal(row, ReleaseColumn.NET_SHARE_PROCEEDS)
        price = _parse_decimal(row, ReleaseColumn.PRICE)
        amount = quantity * price
        symbol = KNOWN_SYMBOL_DICT[plan]
        symbol = TICKER_RENAMES.get(symbol, symbol)

        return BrokerTransaction(
            date=datetime.datetime.strptime(
                row[ReleaseColumn.VEST_DATE], "%d-%b-%Y"
            ).date(),
            action=ActionType.STOCK_ACTIVITY,
            symbol=symbol,
            description=plan,
            quantity=quantity,
            price=price,
            fees=Decimal(0),
            amount=amount,
            currency="USD",
            broker=BROKER_NAME,
        )

    # Morgan Stanley decided to put a notice in the end of the withdrawal report that looks
    # like that:
    # "Please note that any Alphabet share sales, transfers, or deposits that occurred on
    # or prior to the July 15, 2022 stock split are reflected in pre-split. Any sales,
    # transfers, or deposits that occurred after July 15, 2022 are in post-split values.
    # For GSU vests, your activity is displayed in post-split values."
    # It makes sense, but it totally breaks the CSV parsing
    @staticmethod
    def _is_notice(row: dict[str, str]) -> bool:
        row_str = ",".join([c for c in row.values() if c])
        return row_str.startswith("Please note")

    @staticmethod
    def _init_from_withdrawal_report(
        row: dict[str, str], file: Path
    ) -> BrokerTransaction | None:
        if MSSBParser._is_notice(row):
            return None

        plan = row[WithdrawalColumn.PLAN]

        if row[WithdrawalColumn.TYPE] != "Sale":
            raise ParsingError(file, f"Unknown type: {row[WithdrawalColumn.TYPE]}")

        if row[WithdrawalColumn.ORDER_STATUS] != "Complete":
            raise ParsingError(
                file, f"Unknown status: {row[WithdrawalColumn.ORDER_STATUS]}"
            )

        price_raw = row[WithdrawalColumn.PRICE]
        if not price_raw or not price_raw.startswith("$"):
            raise ParsingError(file, f"Unknown price currency: {price_raw}")

        if plan not in KNOWN_SYMBOL_DICT:
            raise ParsingError(file, f"Unknown plan: {plan}")

        quantity = -_parse_decimal(row, WithdrawalColumn.QUANTITY)
        price = _parse_decimal(row, WithdrawalColumn.PRICE)
        amount = _parse_decimal(row, WithdrawalColumn.NET_AMOUNT)
        fees = quantity * price - amount

        if plan == "Cash":
            action = ActionType.TRANSFER
            amount *= -1
        else:
            action = ActionType.SELL

        transaction = BrokerTransaction(
            date=datetime.datetime.strptime(
                row[WithdrawalColumn.EXECUTION_DATE], "%d-%b-%Y"
            ).date(),
            action=action,
            symbol=KNOWN_SYMBOL_DICT[plan],
            description=plan,
            quantity=quantity,
            price=price,
            fees=fees,
            amount=amount,
            currency="USD",
            broker=BROKER_NAME,
        )

        return MSSBParser._handle_stock_split(transaction)

    @staticmethod
    def _handle_stock_split(transaction: BrokerTransaction) -> BrokerTransaction:
        for split in STOCK_SPLIT_INFO:
            if (
                transaction.symbol == split.symbol
                and transaction.action == ActionType.SELL
                and transaction.date < split.date
            ):
                if transaction.quantity:
                    transaction.quantity *= split.factor
                if transaction.price:
                    transaction.price /= split.factor

        return transaction
