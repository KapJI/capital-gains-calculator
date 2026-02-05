"""Charles Schwab parser."""

from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal
from enum import Enum
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO

from cgt_calc.args_validators import DeprecatedAction, existing_file_type
from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import (
    ParsingError,
    SymbolMissingError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
)
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.schwab_cusip_bonds import adjust_cusip_bond_price

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
    from pathlib import Path

OLD_COLUMNS_NUM: Final = 9
NEW_COLUMNS_NUM: Final = 8
LOGGER = logging.getLogger(__name__)

# Cancel Buy search window: Arbitrary time window chosen as a sensible limit for
# how far to search backward from a Cancel Buy to find the original Buy transaction.
# This is not based on any documented Schwab settlement period - just a practical limit.
CANCEL_BUY_SEARCH_DAYS: Final = 5


class SchwabTransactionsFileRequiredHeaders(str, Enum):
    """Enum to list the headers in Schwab transactions file that we will use."""

    DATE = "Date"
    ACTION = "Action"
    SYMBOL = "Symbol"
    DESCRIPTION = "Description"
    PRICE = "Price"
    QUANTITY = "Quantity"
    FEES_AND_COMM = "Fees & Comm"
    AMOUNT = "Amount"


class AwardsTransactionsFileRequiredHeaders(str, Enum):
    """Enum to list the headers in Awards transactions file that we will use."""

    DATE = "Date"
    SYMBOL = "Symbol"
    FAIR_MARKET_VALUE_PRICE = "FairMarketValuePrice"


@dataclass
class AwardPrices:
    """Class to store initial stock prices."""

    award_prices: dict[datetime.date, dict[str, Decimal]]

    def __bool__(self) -> bool:
        """Return True if not empty."""
        return bool(self.award_prices)

    def get(self, date: datetime.date, symbol: str) -> tuple[datetime.date, Decimal]:
        """Get initial stock price at given date."""
        # Award dates may go back for few days, depending on
        # holidays or weekends, so we do a linear search
        # in the past to find the award price
        symbol = TICKER_RENAMES.get(symbol, symbol)
        for i in range(7):
            to_search = date - datetime.timedelta(days=i)

            if (
                to_search in self.award_prices
                and symbol in self.award_prices[to_search]
            ):
                return (to_search, self.award_prices[to_search][symbol])
        raise KeyError(f"Award price is not found for symbol {symbol} for date {date}")


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""
    if label in ["Buy", "Cancel Buy"]:
        return ActionType.BUY

    if label == "Sell":
        return ActionType.SELL

    if label in [
        "MoneyLink Transfer",
        "Misc Cash Entry",
        "Service Fee",
        "Wire Funds",
        "Wire Sent",
        "Funds Received",
        "Journal",
        "Cash In Lieu",
        "Visa Purchase",
        "MoneyLink Deposit",
        "MoneyLink Adj",  # likely a returned transfer
        "Security Transfer",
    ]:
        return ActionType.TRANSFER

    if label == "Stock Plan Activity":
        return ActionType.STOCK_ACTIVITY

    if label in [
        "Qualified Dividend",
        "Cash Dividend",
        "Qual Div Reinvest",
        "Div Adjustment",
        "Special Qual Div",
        "Non-Qualified Div",
    ]:
        return ActionType.DIVIDEND

    if label in ["NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"]:
        return ActionType.DIVIDEND_TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in ["Adjustment", "IRS Withhold Adj", "Wire Funds Adj"]:
        return ActionType.ADJUSTMENT

    if label in ["Short Term Cap Gain", "Long Term Cap Gain"]:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label in ["Credit Interest", "Bond Interest"]:
        return ActionType.INTEREST

    if label == "Reinvest Shares":
        return ActionType.REINVEST_SHARES

    if label == "Reinvest Dividend":
        return ActionType.REINVEST_DIVIDENDS

    if label == "Wire Funds Received":
        return ActionType.WIRE_FUNDS_RECEIVED

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label in ["Cash Merger", "Cash Merger Adj"]:
        return ActionType.CASH_MERGER

    if label in ["Full Redemption", "Full Redemption Adj"]:
        return ActionType.FULL_REDEMPTION

    raise ParsingError(file, f"Unknown action: '{label}'")


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(
        self,
        row_dict: OrderedDict[str, str],
        file: Path,
    ):
        """Create transaction from CSV row."""
        if len(row_dict) < NEW_COLUMNS_NUM or len(row_dict) > OLD_COLUMNS_NUM:
            # Old transactions had empty 9th column.
            raise UnexpectedColumnCountError(
                list(row_dict.values()), NEW_COLUMNS_NUM, file
            )
        if len(row_dict) == OLD_COLUMNS_NUM and list(row_dict.values())[-1] != "":
            raise ParsingError(file, f"Column {OLD_COLUMNS_NUM} should be empty")
        as_of_str = " as of "
        date_header = SchwabTransactionsFileRequiredHeaders.DATE.value
        if as_of_str in row_dict[date_header]:
            index = row_dict[date_header].find(as_of_str)
            date_str = row_dict[date_header][:index]
        else:
            date_str = row_dict[date_header]
        try:
            date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        except ValueError as exc:
            raise ParsingError(
                file, f"Invalid date format: {date_str} from row: {row_dict}"
            ) from exc
        action_header = SchwabTransactionsFileRequiredHeaders.ACTION.value
        self.raw_action = row_dict[action_header]
        action = action_from_str(self.raw_action, file)
        symbol_header = SchwabTransactionsFileRequiredHeaders.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description_header = SchwabTransactionsFileRequiredHeaders.DESCRIPTION.value
        description = row_dict[description_header]
        if (
            action == ActionType.DIVIDEND_TAX
            and symbol is None
            and "SCHWAB1 INT" in description
        ):
            action = ActionType.INTEREST_TAX
        price_header = SchwabTransactionsFileRequiredHeaders.PRICE.value
        price = (
            Decimal(row_dict[price_header].replace("$", "").replace(",", ""))
            if row_dict[price_header] != ""
            else None
        )
        quantity_header = SchwabTransactionsFileRequiredHeaders.QUANTITY.value
        quantity = (
            Decimal(row_dict[quantity_header].replace(",", ""))
            if row_dict[quantity_header] != ""
            else None
        )
        fees_header = SchwabTransactionsFileRequiredHeaders.FEES_AND_COMM.value
        fees = (
            Decimal(row_dict[fees_header].replace("$", ""))
            if row_dict[fees_header] != ""
            else Decimal(0)
        )
        amount_header = SchwabTransactionsFileRequiredHeaders.AMOUNT.value
        amount = (
            Decimal(row_dict[amount_header].replace("$", ""))
            if row_dict[amount_header] != ""
            else None
        )

        # Handle bonds/notes: CUSIP symbols have price per $100 face value
        price, fees = adjust_cusip_bond_price(symbol, price, quantity, amount, fees)

        currency = "USD"
        broker = "Charles Schwab"
        super().__init__(
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
        )

    @staticmethod
    def create(
        row_dict: OrderedDict[str, str], file: Path, awards_prices: AwardPrices
    ) -> SchwabTransaction:
        """Create and post process a SchwabTransaction."""
        transaction = SchwabTransaction(row_dict, file)
        if (
            transaction.price is None
            and transaction.action == ActionType.STOCK_ACTIVITY
        ):
            symbol = transaction.symbol
            if symbol is None:
                raise SymbolMissingError(transaction)
            # Schwab transaction list contains sometimes incorrect date
            # for awards which don't match the PDF statements.
            # We want to make sure to match date and price form the awards
            # spreadsheet.
            _vest_date, transaction.price = awards_prices.get(transaction.date, symbol)
        return transaction


def _combine_cash_merger_pair(
    cash_merger: SchwabTransaction,
    cash_merger_adj: SchwabTransaction,
    transactions_file: Path,
) -> SchwabTransaction:
    """Combine Cash Merger + Cash Merger Adj into single transaction.

    Cash Merger: Has Amount (proceeds), No Quantity/Price
    Cash Merger Adj: Has Quantity (shares), No Amount

    Returns: Unified transaction with calculated price
    """
    try:
        # Validate matching fields
        assert cash_merger.description == cash_merger_adj.description
        assert cash_merger.symbol == cash_merger_adj.symbol
        assert cash_merger.date == cash_merger_adj.date

        # Validate data pattern: Cash Merger has amount, Adj has quantity
        assert cash_merger.amount is not None, "Cash Merger must have Amount"
        assert cash_merger.quantity is None, "Cash Merger should not have Quantity"
        assert cash_merger.price is None, "Cash Merger should not have Price"

        assert cash_merger_adj.quantity is not None, (
            "Cash Merger Adj must have Quantity"
        )
        assert cash_merger_adj.amount is None, "Cash Merger Adj should not have Amount"

    except AssertionError as err:
        raise ParsingError(
            transactions_file,
            f"Invalid Cash Merger format: {cash_merger.raw_action}, "
            "run with --verbose for more details",
        ) from err

    # Create unified transaction
    unified = cash_merger
    # Quantity is negative (shares leaving), convert to positive for SELL
    unified.quantity = -1 * cash_merger_adj.quantity
    # Mypy: at this point unified.amount and unified.quantity are guaranteed non-None
    assert unified.amount is not None
    assert unified.quantity is not None
    unified.price = unified.amount / unified.quantity
    unified.fees += cash_merger_adj.fees

    return unified


def _combine_full_redemption_pair(
    full_redemption_adj: SchwabTransaction,
    full_redemption: SchwabTransaction,
    transactions_file: Path,
) -> SchwabTransaction:
    """Combine Full Redemption Adj + Full Redemption into single transaction.

    Actual Schwab CSV format:
    Full Redemption Adj: Has Amount (proceeds), No Price/Quantity
    Full Redemption: Has Quantity (shares), No Price/Amount

    Returns: Unified transaction with calculated price
    """
    try:
        # Validate matching fields
        assert full_redemption_adj.description == full_redemption.description
        assert full_redemption_adj.symbol == full_redemption.symbol
        assert full_redemption_adj.date == full_redemption.date

        # Validate data pattern: Adj has amount, Full Redemption has quantity
        assert full_redemption_adj.amount is not None, (
            "Full Redemption Adj must have Amount"
        )
        assert full_redemption_adj.quantity is None, (
            "Full Redemption Adj should not have Quantity"
        )
        assert full_redemption_adj.price is None, (
            "Full Redemption Adj should not have Price"
        )

        assert full_redemption.quantity is not None, (
            "Full Redemption must have Quantity"
        )
        assert full_redemption.price is None, "Full Redemption should not have Price"
        assert full_redemption.amount is None, "Full Redemption should not have Amount"

    except AssertionError as err:
        raise ParsingError(
            transactions_file,
            f"Invalid Full Redemption format: {full_redemption.raw_action}, "
            "run with --verbose for more details",
        ) from err

    # Create unified transaction (use Full Redemption as base for action type)
    unified = full_redemption
    # Quantity is negative (shares leaving), convert to positive
    unified.quantity = -1 * full_redemption.quantity
    unified.amount = full_redemption_adj.amount
    # Mypy: at this point unified.amount and unified.quantity are guaranteed non-None
    assert unified.amount is not None
    assert unified.quantity is not None
    unified.price = unified.amount / unified.quantity
    unified.fees += full_redemption_adj.fees

    return unified


def _unify_schwab_paired_transactions(
    transactions: list[SchwabTransaction],
    transactions_file: Path,
) -> list[SchwabTransaction]:
    """Unify paired transactions (Cash Merger and Full Redemption).

    Both follow a similar pattern where transactions are split into two rows:
    1. One row has the amount (proceeds)
    2. Other row has the quantity (shares)
    We combine them to calculate the price per share.

    Cash Merger pattern:
        Row 1: "Cash Merger" - Has Amount ($1000), No Quantity/Price
        Row 2: "Cash Merger Adj" - Has Quantity (-100 shares), No Amount
        Result: Sell 100 shares at $10/share

    Full Redemption pattern:
        Row 1: "Full Redemption Adj" - Has Quantity (-100 shares), No Amount
        Row 2: "Full Redemption" - Has Amount ($1000), No Quantity/Price
        Result: Sell 100 shares at $10/share
    """
    filtered: list[SchwabTransaction] = []
    i = 0
    while i < len(transactions):
        transaction = transactions[i]

        if transaction.raw_action == "Cash Merger Adj":
            # Cash Merger Adj comes AFTER Cash Merger
            assert len(filtered) > 0, (
                "Cash Merger Adj must be preceded by a Cash Merger transaction"
            )
            main_transaction = filtered[-1]
            adj_transaction = transaction

            # Validate it's a Cash Merger pair
            assert main_transaction.raw_action == "Cash Merger", (
                "Cash Merger Adj must follow Cash Merger"
            )

            unified = _combine_cash_merger_pair(
                main_transaction, adj_transaction, transactions_file
            )

            # Replace the previous Cash Merger with unified transaction
            filtered[-1] = unified
            LOGGER.warning(
                "Cash Merger support is not complete and doesn't cover the "
                "cases when shares are received aside from cash, "
                "please review this transaction carefully: %s",
                unified,
            )

        elif transaction.raw_action == "Full Redemption Adj":
            # Full Redemption Adj comes BEFORE Full Redemption
            assert i + 1 < len(transactions), (
                "Full Redemption Adj must be followed by a Full Redemption transaction"
            )
            adj_transaction = transaction
            main_transaction = transactions[i + 1]

            # Validate it's a Full Redemption pair
            assert main_transaction.raw_action == "Full Redemption", (
                "Full Redemption Adj must be followed by Full Redemption"
            )

            unified = _combine_full_redemption_pair(
                adj_transaction, main_transaction, transactions_file
            )

            # Add unified transaction and skip next (Full Redemption)
            filtered.append(unified)
            i += 1  # Skip the Full Redemption transaction
            LOGGER.warning(
                "Full Redemption combined with adjustment: %s",
                unified,
            )

        else:
            filtered.append(transaction)

        i += 1

    return filtered


def _filter_cancelled_buy_transactions(
    transactions: list[SchwabTransaction],
) -> list[SchwabTransaction]:
    """Filter out Cancel Buy transactions and their matching Buy transactions.

    Schwab reports both the original Buy and a "Cancel Buy" transaction when a
    purchase is cancelled. Both need to be removed to avoid incorrect capital
    gains calculations.

    This is a Schwab-specific quirk - other brokers may not report cancellations
    at all or may handle them differently.

    Args:
        transactions: List of parsed Schwab transactions

    Returns:
        Filtered list with Cancel Buy pairs removed

    """
    indices_to_remove: set[int] = set()

    # Find all Cancel Buy transactions
    for cancel_idx, transaction in enumerate(transactions):
        if transaction.raw_action != "Cancel Buy":
            continue

        # Already marked for removal
        if cancel_idx in indices_to_remove:
            continue

        # Search backward for matching Buy within search window
        for buy_idx in range(cancel_idx - 1, -1, -1):
            buy_txn = transactions[buy_idx]

            # Stop if beyond search window
            if abs((buy_txn.date - transaction.date).days) > CANCEL_BUY_SEARCH_DAYS:
                break

            # Skip if already marked for removal
            if buy_idx in indices_to_remove:
                continue

            # Check if this is the matching Buy transaction
            if (
                buy_txn.action == ActionType.BUY
                and buy_txn.symbol == transaction.symbol
                and buy_txn.quantity == transaction.quantity
                and buy_txn.price == transaction.price
            ):
                # Found matching pair - mark both for removal
                indices_to_remove.add(cancel_idx)
                indices_to_remove.add(buy_idx)
                LOGGER.info(
                    "Matched Cancel Buy with original Buy: symbol=%s, qty=%s, "
                    "price=%s, buy_date=%s, cancel_date=%s",
                    buy_txn.symbol,
                    buy_txn.quantity,
                    buy_txn.price,
                    buy_txn.date,
                    transaction.date,
                )
                break
        else:
            # No matching Buy found
            LOGGER.warning(
                "Could not find matching Buy for Cancel Buy: %s",
                transaction,
            )

    if len(indices_to_remove) > 0:
        LOGGER.info(
            "Removed %d cancelled transaction(s) and their originals",
            len(indices_to_remove),
        )

    # Return filtered list
    return [txn for i, txn in enumerate(transactions) if i not in indices_to_remove]


def _read_schwab_awards(
    schwab_award_transactions_file: Path | None,
) -> AwardPrices:
    """Read initial stock prices from CSV file."""
    if schwab_award_transactions_file is None:
        return AwardPrices(award_prices={})

    initial_prices: dict[datetime.date, dict[str, Decimal]] = defaultdict(dict)
    headers = []
    lines = []

    with schwab_award_transactions_file.open(encoding="utf-8") as csv_file:
        print(f"Parsing {schwab_award_transactions_file}...")
        lines = list(csv.reader(csv_file))
    if not lines:
        raise ParsingError(
            schwab_award_transactions_file, "Charles Schwab Award CSV file is empty"
        )
    headers = lines[0]
    required_headers = set(
        {header.value for header in AwardsTransactionsFileRequiredHeaders}
    )
    if not required_headers.issubset(headers):
        raise ParsingError(
            schwab_award_transactions_file,
            f"Missing columns in awards file: {required_headers.difference(headers)}",
        )

    # Remove headers
    lines = lines[1:]

    modulo = len(lines) % 2
    if modulo != 0:
        raise UnexpectedRowCountError(
            len(lines) - modulo + 2, schwab_award_transactions_file
        )

    for upper_row, lower_row in zip(lines[::2], lines[1::2], strict=True):
        # in this format each row is split into two rows,
        # so we combine them safely below
        row = []
        for upper_col, lower_col in zip(upper_row, lower_row, strict=True):
            assert upper_col == "" or lower_col == ""
            row.append(upper_col + lower_col)

        if len(row) != len(headers):
            raise UnexpectedColumnCountError(
                row, len(headers), schwab_award_transactions_file
            )

        row_dict = OrderedDict(zip(headers, row, strict=True))
        date_header = AwardsTransactionsFileRequiredHeaders.DATE.value
        date_str = row_dict[date_header]
        try:
            date = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        except ValueError:
            date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        symbol_header = AwardsTransactionsFileRequiredHeaders.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        fair_market_value_price_header = (
            AwardsTransactionsFileRequiredHeaders.FAIR_MARKET_VALUE_PRICE.value
        )
        price = (
            Decimal(row_dict[fair_market_value_price_header].replace("$", ""))
            if row_dict[fair_market_value_price_header] != ""
            else None
        )
        if symbol is not None and price is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
            initial_prices[date][symbol] = price
    return AwardPrices(award_prices=dict(initial_prices))


class SchwabParser(BaseSingleFileParser):
    """Parser for RAW format transaction files."""

    arg_name = "schwab"
    pretty_name = "Charles Schwab"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--schwab"]

    awards_prices: AwardPrices = _read_schwab_awards(None)

    @classmethod
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        arg_group.add_argument(
            "--schwab-award-file",
            type=existing_file_type,
            default=None,
            metavar="PATH",
            help="Charles Schwab Equity Awards transaction history in CSV format",
        )
        arg_group.add_argument(
            "--schwab-award",
            action=DeprecatedAction,
            dest="schwab_award_file",
            type=existing_file_type,
            help=argparse.SUPPRESS,
        )
        super().register_arguments(arg_group)

    @classmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        award_path = args.schwab_award_file
        cls.awards_prices = _read_schwab_awards(award_path)
        return super().load_from_args(args)

    @classmethod
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read Schwab transactions from file."""

        lines = list(csv.reader(file))
        if not lines:
            raise ParsingError(
                file_path, "Charles Schwab transactions CSV file is empty"
            )
        if not cls.awards_prices:
            LOGGER.warning("No Schwab Award file provided")
        headers = lines[0]

        required_headers = set(
            {header.value for header in SchwabTransactionsFileRequiredHeaders}
        )
        if not required_headers.issubset(headers):
            raise ParsingError(
                file_path,
                "Missing columns in Schwab transaction file: "
                f"{required_headers.difference(headers)}",
            )

        # Remove header
        lines = lines[1:]
        transactions = [
            SchwabTransaction.create(
                OrderedDict(zip(headers, row, strict=True)),
                file_path,
                cls.awards_prices,
            )
            for row in lines
            if any(row)
        ]
        transactions = _unify_schwab_paired_transactions(transactions, file_path)
        transactions = _filter_cancelled_buy_transactions(transactions)
        transactions.reverse()
        return list(transactions)
