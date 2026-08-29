"""Charles Schwab parser."""

from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

import shtab

from cgt_calc.args_validators import DeprecatedAction, existing_file_type, set_completer
from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import (
    ParsingError,
    SymbolMissingError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
)
from cgt_calc.logging import parsing_msg
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
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


class RequiredTransactionsColumn(StrEnum):
    """Column names for Schwab transactions file."""

    DATE = "Date"
    ACTION = "Action"
    SYMBOL = "Symbol"
    DESCRIPTION = "Description"
    PRICE = "Price"
    QUANTITY = "Quantity"
    FEES_AND_COMM = "Fees & Comm"
    AMOUNT = "Amount"


class RequiredAwardColumn(StrEnum):
    """Column names for Awards transactions file."""

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
        # Award dates may go back a few days, depending on
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
    if label == "Buy":
        return ActionType.BUY

    if label == "Cancel Buy":
        # Not a purchase: it is dropped along with the Buy it reverses, and
        # a cancellation with no matching Buy fails the run.
        return ActionType.CANCEL_BUY

    if label == "Sell":
        return ActionType.SELL

    if label in {
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
    }:
        return ActionType.TRANSFER

    if label == "Stock Plan Activity":
        return ActionType.STOCK_ACTIVITY

    if label in {
        "Qualified Dividend",
        "Cash Dividend",
        "Qual Div Reinvest",
        "Div Adjustment",
        "Special Qual Div",
        "Non-Qualified Div",
    }:
        return ActionType.DIVIDEND

    if label in {"NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"}:
        return ActionType.DIVIDEND_TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in {"Adjustment", "IRS Withhold Adj", "Wire Funds Adj"}:
        return ActionType.ADJUSTMENT

    if label in {"Short Term Cap Gain", "Long Term Cap Gain"}:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label in {"Credit Interest", "Bond Interest"}:
        return ActionType.INTEREST

    if label == "Reinvest Shares":
        return ActionType.REINVEST_SHARES

    if label == "Reinvest Dividend":
        return ActionType.REINVEST_DIVIDENDS

    if label == "Wire Funds Received":
        return ActionType.WIRE_FUNDS_RECEIVED

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label in {"Cash Merger", "Cash Merger Adj"}:
        return ActionType.CASH_MERGER

    if label in {"Full Redemption", "Full Redemption Adj"}:
        return ActionType.FULL_REDEMPTION

    raise ParsingError(file, f"Unknown action: '{label}'")


def parse_decimal(
    row: OrderedDict[str, str],
    column: RequiredTransactionsColumn,
) -> Decimal | None:
    """Convert Schwab CSV column into Decimal, allowing optional blanks."""

    raw_value = row[column]

    normalized = raw_value.replace("$", "").replace(",", "").strip()
    if normalized == "":
        return None

    try:
        return Decimal(normalized)
    except InvalidOperation as err:
        raise ValueError(
            f"Invalid decimal in column '{column.value}': {raw_value!r}"
        ) from err


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
        date_header = RequiredTransactionsColumn.DATE.value
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
        action_header = RequiredTransactionsColumn.ACTION.value
        self.raw_action = row_dict[action_header]
        action = action_from_str(self.raw_action, file)
        symbol_header = RequiredTransactionsColumn.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row_dict[RequiredTransactionsColumn.DESCRIPTION.value]
        if (
            action == ActionType.DIVIDEND_TAX
            and symbol is None
            and "SCHWAB1 INT" in description
        ):
            # Withholding on account-level cash interest, not tied to a security.
            action = ActionType.INTEREST_TAX
        price = parse_decimal(row_dict, RequiredTransactionsColumn.PRICE)
        quantity = parse_decimal(row_dict, RequiredTransactionsColumn.QUANTITY)
        fees = parse_decimal(
            row_dict, RequiredTransactionsColumn.FEES_AND_COMM
        ) or Decimal(0)
        amount = parse_decimal(row_dict, RequiredTransactionsColumn.AMOUNT)

        # Handle bonds/notes: CUSIP symbols have price per $100 face value
        price, fees = adjust_cusip_bond_price(symbol, price, quantity, amount, fees)

        currency = CurrencyCode("USD")
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
            # The Schwab transaction list sometimes contains an incorrect date
            # for awards which doesn't match the PDF statements.
            # We want to make sure to match date and price from the awards
            # spreadsheet.
            try:
                _vest_date, transaction.price = awards_prices.get(
                    transaction.date, symbol
                )
            except KeyError as err:
                context = (
                    f"the {symbol} stock activity of {transaction.quantity} "
                    f"on {transaction.date} has no price of its own"
                )
                reason = (
                    "no Schwab Award file was provided"
                    if not awards_prices
                    else "the Schwab Award file has no award price for it"
                )
                raise ParsingError(
                    file,
                    f"Cannot price a vest: {context}, and {reason}. "
                    "Pass the Equity Awards transaction history with "
                    "--schwab-award-file.",
                ) from err
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
    if (
        cash_merger.description != cash_merger_adj.description
        or cash_merger.symbol != cash_merger_adj.symbol
        or cash_merger.date != cash_merger_adj.date
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Cash Merger format for {cash_merger.symbol} on "
            f"{cash_merger.date}: the Cash Merger and adjustment rows must "
            "have the same date, symbol and description",
        )

    amount = cash_merger.amount
    adjustment_quantity = cash_merger_adj.quantity
    if (
        amount is None
        or not amount.is_finite()
        or amount < 0
        or cash_merger.quantity is not None
        or cash_merger.price is not None
        or adjustment_quantity is None
        or not adjustment_quantity.is_finite()
        or adjustment_quantity >= 0
        or cash_merger_adj.amount is not None
        or cash_merger_adj.price is not None
        or not cash_merger.fees.is_finite()
        or not cash_merger_adj.fees.is_finite()
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Cash Merger format for {cash_merger.symbol} on "
            f"{cash_merger.date}: expected non-negative proceeds only on the "
            "Cash Merger row and a negative share quantity only on the "
            "adjustment row",
        )

    # Create unified transaction
    unified = cash_merger
    # Quantity is negative (shares leaving), convert to positive for SELL
    unified.quantity = -adjustment_quantity
    unified.price = amount / unified.quantity
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
    if (
        full_redemption_adj.description != full_redemption.description
        or full_redemption_adj.symbol != full_redemption.symbol
        or full_redemption_adj.date != full_redemption.date
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Full Redemption format for {full_redemption.symbol} on "
            f"{full_redemption.date}: the adjustment and redemption rows must "
            "have the same date, symbol and description",
        )

    amount = full_redemption_adj.amount
    redemption_quantity = full_redemption.quantity
    if (
        amount is None
        or not amount.is_finite()
        or amount < 0
        or full_redemption_adj.quantity is not None
        or full_redemption_adj.price is not None
        or redemption_quantity is None
        or not redemption_quantity.is_finite()
        or redemption_quantity >= 0
        or full_redemption.price is not None
        or full_redemption.amount is not None
        or not full_redemption_adj.fees.is_finite()
        or not full_redemption.fees.is_finite()
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Full Redemption format for {full_redemption.symbol} on "
            f"{full_redemption.date}: expected non-negative proceeds only on "
            "the adjustment row and a negative share quantity only on the "
            "redemption row",
        )

    # Create unified transaction (use Full Redemption as base for action type)
    unified = full_redemption
    # Quantity is negative (shares leaving), convert to positive
    unified.quantity = -redemption_quantity
    unified.amount = amount
    unified.price = amount / unified.quantity
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
            if len(filtered) == 0:
                raise ParsingError(
                    transactions_file,
                    "Cash Merger Adj must be preceded by a Cash Merger transaction",
                )
            main_transaction = filtered[-1]
            adj_transaction = transaction

            if main_transaction.raw_action != "Cash Merger":
                raise ParsingError(
                    transactions_file,
                    "Cash Merger Adj must immediately follow a Cash Merger transaction",
                )

            unified = _combine_cash_merger_pair(
                main_transaction, adj_transaction, transactions_file
            )

            # Replace the previous Cash Merger with unified transaction
            filtered[-1] = unified
            LOGGER.warning(
                "Cash Merger support is not complete and doesn't cover the "
                "cases when shares are received aside from cash, "
                "please review this transaction carefully:\n"
                "    %s: %s units on %s for %s %s (%s)",
                unified.symbol,
                unified.quantity,
                unified.date,
                unified.amount,
                unified.currency,
                unified.broker,
            )

        elif transaction.raw_action == "Full Redemption Adj":
            # Full Redemption Adj comes BEFORE Full Redemption
            if i + 1 >= len(transactions):
                raise ParsingError(
                    transactions_file,
                    "Full Redemption Adj must be followed by a Full Redemption "
                    "transaction",
                )
            adj_transaction = transaction
            main_transaction = transactions[i + 1]

            if main_transaction.raw_action != "Full Redemption":
                raise ParsingError(
                    transactions_file,
                    "Full Redemption Adj must be followed by a Full Redemption "
                    "transaction",
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


def _is_matching_buy(buy_txn: SchwabTransaction, cancel_txn: SchwabTransaction) -> bool:
    """Whether buy_txn is the purchase that cancel_txn reverses.

    The raw action is compared rather than the action type, so a cancellation
    can never pair with another cancellation.
    """
    return (
        buy_txn.raw_action == "Buy"
        and buy_txn.symbol == cancel_txn.symbol
        and buy_txn.quantity == cancel_txn.quantity
        and buy_txn.price == cancel_txn.price
    )


def _find_matching_buy(
    transactions: list[SchwabTransaction],
    cancel_idx: int,
    consumed: set[int],
) -> int | None:
    """Find the index of the Buy that the Cancel Buy at cancel_idx reverses.

    The list is newest-first, so the purchase being reversed normally sits at
    a higher index than the cancellation. When the two share a date the export
    orders them arbitrarily, so the purchase can equally sit just above it;
    those rows are searched as well. A purchase on a date later than the
    cancellation is never a match, because it had not happened yet when the
    cancellation was recorded.

    Args:
        transactions: Parsed Schwab transactions, newest-first
        cancel_idx: Index of the Cancel Buy to reconcile
        consumed: Indices already claimed by an earlier pair

    Returns:
        Index of the matching Buy, or None when there is none

    """
    cancel_txn = transactions[cancel_idx]

    # Older rows, out to the edge of the search window.
    for buy_idx in range(cancel_idx + 1, len(transactions)):
        buy_txn = transactions[buy_idx]
        if abs((buy_txn.date - cancel_txn.date).days) > CANCEL_BUY_SEARCH_DAYS:
            break
        if buy_idx not in consumed and _is_matching_buy(buy_txn, cancel_txn):
            return buy_idx

    # Newer rows, but only the ones sharing the cancellation's own date.
    for buy_idx in range(cancel_idx - 1, -1, -1):
        buy_txn = transactions[buy_idx]
        if buy_txn.date != cancel_txn.date:
            break
        if buy_idx not in consumed and _is_matching_buy(buy_txn, cancel_txn):
            return buy_idx

    return None


def _filter_cancelled_buy_transactions(
    transactions: list[SchwabTransaction],
    file: Path,
) -> list[SchwabTransaction]:
    """Filter out Cancel Buy transactions and their matching Buy transactions.

    Schwab reports both the original Buy and a "Cancel Buy" transaction when a
    purchase is cancelled. Both need to be removed to avoid incorrect capital
    gains calculations.

    This is a Schwab-specific quirk - other brokers may not report cancellations
    at all or may handle them differently.

    Note: this runs on the raw, newest-first ordered list of transactions as
    read from the Schwab export (before ``transactions.reverse()`` is applied
    by the caller). A Cancel Buy row is chronologically *after* the Buy it
    cancels, so in a newest-first list the Cancel Buy normally has a *lower*
    index than its Buy. Same-date rows are the exception, since the export
    orders those arbitrarily; see ``_find_matching_buy`` for how both are
    searched.

    Args:
        transactions: List of parsed Schwab transactions, newest-first
        file: Source file, for error reporting

    Returns:
        Filtered list with Cancel Buy pairs removed

    Raises:
        ParsingError: If a Cancel Buy has no matching Buy

    """
    indices_to_remove: set[int] = set()

    # Find all Cancel Buy transactions
    for cancel_idx, transaction in enumerate(transactions):
        if transaction.raw_action != "Cancel Buy":
            continue

        # Already marked for removal
        if cancel_idx in indices_to_remove:
            continue

        buy_idx = _find_matching_buy(transactions, cancel_idx, indices_to_remove)

        if buy_idx is not None:
            # Found matching pair - mark both for removal
            indices_to_remove.add(cancel_idx)
            indices_to_remove.add(buy_idx)
            buy_txn = transactions[buy_idx]
            LOGGER.debug(
                "Matched Cancel Buy with original Buy: symbol=%s, qty=%s, "
                "price=%s, buy_date=%s, cancel_date=%s",
                buy_txn.symbol,
                buy_txn.quantity,
                buy_txn.price,
                buy_txn.date,
                transaction.date,
            )
        else:
            # A cancellation with nothing to reverse means the export does not
            # reach back to the purchase, or that purchase settled outside the
            # window above. Either way the pair cannot be reconciled and the
            # row cannot be processed, so refuse here, where the row's meaning
            # is still known. Left in place it would reach the calculator,
            # which refuses any action it does not handle but can only report
            # an unprocessed action type, naming neither the cancellation nor
            # what to do about it.
            raise ParsingError(
                file,
                f"Found a {transaction.raw_action} for {transaction.symbol} on "
                f"{transaction.date} with no Buy to match it in the "
                f"{CANCEL_BUY_SEARCH_DAYS} days before. The purchase may be "
                "missing from the export, older than that window, recorded "
                "with a different quantity or price, or already reversed by "
                "another cancellation. Remove this row; remove the purchase "
                "with it only if the purchase is still in the export and no "
                "other cancellation reverses it.",
            )

    if indices_to_remove:
        LOGGER.info(
            "Removed %d cancelled transaction(s) and their originals",
            len(indices_to_remove),
        )

    return [txn for i, txn in enumerate(transactions) if i not in indices_to_remove]


def _read_schwab_awards(
    schwab_award_transactions_file: Path | None,
) -> AwardPrices:
    """Read initial stock prices from CSV file."""
    if schwab_award_transactions_file is None:
        return AwardPrices(award_prices={})

    initial_prices: dict[datetime.date, dict[str, Decimal]] = defaultdict(dict)

    with schwab_award_transactions_file.open(encoding="utf-8") as csv_file:
        parsing_msg(schwab_award_transactions_file)
        lines = list(csv.reader(csv_file))
    if not lines:
        raise ParsingError(
            schwab_award_transactions_file, "Charles Schwab Award CSV file is empty"
        )
    header = lines[0]
    required_columns = {column.value for column in RequiredAwardColumn}
    if not required_columns.issubset(header):
        raise ParsingError(
            schwab_award_transactions_file,
            "Missing columns in Schwab Award file: "
            f"{required_columns.difference(header)}",
            row_index=1,
        )

    # Remove header
    lines = lines[1:]

    modulo = len(lines) % 2
    if modulo != 0:
        raise UnexpectedRowCountError(
            len(lines) - modulo + 2, schwab_award_transactions_file
        )

    for offset in range(0, len(lines), 2):
        upper_row = lines[offset]
        lower_row = lines[offset + 1]
        row_index = offset + 2

        if len(upper_row) != len(header):
            raise UnexpectedColumnCountError(
                upper_row,
                len(header),
                schwab_award_transactions_file,
                row_index=row_index,
            )
        # Some exports append an extra empty column to the lower row.
        if len(lower_row) == len(header) + 1 and lower_row[-1] == "":
            lower_row.pop()
        if len(lower_row) != len(header):
            raise UnexpectedColumnCountError(
                lower_row,
                len(header),
                schwab_award_transactions_file,
                row_index=row_index + 1,
            )

        # in this format each logical row is split into two rows,
        # so we combine them safely below
        row = []
        for column_index, (upper_col, lower_col) in enumerate(
            zip(upper_row, lower_row, strict=True), start=1
        ):
            if upper_col and lower_col:
                raise ParsingError(
                    schwab_award_transactions_file,
                    f"Both halves of the split award row (rows {row_index} and "
                    f"{row_index + 1}) contain data in column {column_index}",
                    row_index=row_index,
                )
            row.append(upper_col + lower_col)

        row_dict = OrderedDict(zip(header, row, strict=True))
        date_str = row_dict[RequiredAwardColumn.DATE.value]
        try:
            date = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        except ValueError:
            date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        symbol_header = RequiredAwardColumn.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        fair_market_value_price_header = (
            RequiredAwardColumn.FAIR_MARKET_VALUE_PRICE.value
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


class SchwabParser(BaseSingleFileParser[SchwabTransaction]):
    """Parser for Charles Schwab transaction files."""

    arg_name = "schwab"
    pretty_name = "Charles Schwab"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--schwab"]

    awards_prices: AwardPrices = _read_schwab_awards(None)

    @classmethod
    @override
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        set_completer(
            arg_group.add_argument(
                "--schwab-award-file",
                type=existing_file_type,
                default=None,
                metavar="PATH",
                help="Charles Schwab Equity Awards transaction history in CSV format",
            ),
            shtab.FILE,
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
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        award_path = args.schwab_award_file
        cls.awards_prices = _read_schwab_awards(award_path)
        return super().load_from_args(args)

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[SchwabTransaction]:
        """Read Schwab transactions from file."""

        lines = list(csv.reader(file))
        if not lines:
            raise ParsingError(
                file_path, "Charles Schwab transactions CSV file is empty"
            )
        header = lines[0]

        required_headers = {column.value for column in RequiredTransactionsColumn}
        if not required_headers.issubset(header):
            raise ParsingError(
                file_path,
                "Missing columns in Schwab transaction file: "
                f"{required_headers.difference(header)}",
                row_index=1,
            )

        transactions: list[SchwabTransaction] = []
        for index, row in enumerate(lines[1:], start=2):
            if not any(row):
                continue

            if len(row) != len(header):
                raise UnexpectedColumnCountError(
                    row, len(header), file_path, row_index=index
                )

            try:
                transaction = SchwabTransaction.create(
                    OrderedDict(zip(header, row, strict=True)),
                    file_path,
                    cls.awards_prices,
                )
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err

            transactions.append(transaction)
        transactions = _unify_schwab_paired_transactions(transactions, file_path)
        transactions = _filter_cancelled_buy_transactions(transactions, file_path)
        transactions.reverse()
        return transactions
