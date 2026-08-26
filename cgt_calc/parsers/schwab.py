"""Charles Schwab parser."""

from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import csv
from dataclasses import dataclass, replace
import datetime
from decimal import Decimal
from enum import StrEnum
import itertools
import logging
import re
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

import shtab

from cgt_calc.args_validators import (
    DeprecatedAction,
    existing_directory_type,
    existing_file_type,
    set_completer,
)
from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import (
    CgtError,
    ParsingError,
    SymbolMissingError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
)
from cgt_calc.logging import parsing_msg
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalAdjustment,
    CurrencyCode,
    DatedAmount,
    OptionContract,
    OptionType,
    TransactionSource,
    WrittenOptionTaxData,
)
from cgt_calc.parsers.schwab_cusip_bonds import adjust_cusip_bond_price
from cgt_calc.util import approx_equal, parse_decimal

from .base_parsers import BaseSingleFileParser, next_account_token

if TYPE_CHECKING:
    from pathlib import Path

OLD_COLUMNS_NUM: Final = 9
NEW_COLUMNS_NUM: Final = 8
LOGGER = logging.getLogger(__name__)

# Cancel Buy search window: Arbitrary time window chosen as a sensible limit for
# how far to search backward from a Cancel Buy to find the original Buy transaction.
# This is not based on any documented Schwab settlement period - just a practical limit.
CANCEL_BUY_SEARCH_DAYS: Final = 5
OPTION_ASSIGNMENT_SHARES: Final = Decimal(100)
OPTION_ASSIGNMENT_MATCH_DAYS: Final = 3

_HUMAN_OPTION_SYMBOL: Final = re.compile(
    r"^\s*(?P<underlying>[A-Z][A-Z0-9.\-]{0,9})\s+"
    r"(?P<expiry>\d{2}/\d{2}/\d{4})\s+"
    r"\$?(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<option_type>[CP])\s*$",
    re.IGNORECASE,
)
_OCC_OPTION_SYMBOL: Final = re.compile(
    r"^\s*(?P<underlying>[A-Z][A-Z0-9.\-]{0,9})\s*"
    r"(?P<expiry>\d{6})(?P<option_type>[CP])"
    r"(?P<strike>\d{8})\s*$",
    re.IGNORECASE,
)



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

    if label == "Sell to Open":
        return ActionType.OPTION_GRANT

    if label == "Buy to Close":
        return ActionType.OPTION_CLOSE

    if label == "Expired":
        return ActionType.OPTION_EXPIRY

    if label == "Assigned":
        return ActionType.OPTION_ASSIGNMENT

    if label in {"Buy to Open", "Sell to Close", "Exercised"}:
        raise ParsingError(
            file,
            f"Schwab action '{label}' is a purchased-option transaction. "
            "Only written equity options (Sell to Open followed by Buy to "
            "Close, Expired or Assigned) are currently supported.",
        )

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


def _parse_decimal(
    row: OrderedDict[str, str],
    column: RequiredTransactionsColumn,
) -> Decimal | None:
    """Convert Schwab CSV column into Decimal, allowing optional blanks."""

    raw_value = row[column]
    if raw_value.strip(" $,") == "":
        return None

    # An option debit is written in accounting notation: (100.65) is -100.65.
    value = raw_value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = f"-{value[1:-1]}"

    return parse_decimal(value, f"column '{column.value}'", strip="$,")


def parse_option_contract(symbol: str) -> OptionContract | None:
    """Parse Schwab's human-readable or OCC equity-option symbol."""
    human_match = _HUMAN_OPTION_SYMBOL.fullmatch(symbol)
    if human_match:
        expiry = datetime.datetime.strptime(
            human_match.group("expiry"), "%m/%d/%Y"
        ).date()
        strike = Decimal(human_match.group("strike"))
        option_type = (
            OptionType.CALL
            if human_match.group("option_type").upper() == "C"
            else OptionType.PUT
        )
        underlying = human_match.group("underlying").upper()
    else:
        occ_match = _OCC_OPTION_SYMBOL.fullmatch(symbol)
        if not occ_match:
            return None
        expiry = datetime.datetime.strptime(occ_match.group("expiry"), "%y%m%d").date()
        strike = Decimal(occ_match.group("strike")) / Decimal(1000)
        option_type = (
            OptionType.CALL
            if occ_match.group("option_type").upper() == "C"
            else OptionType.PUT
        )
        underlying = occ_match.group("underlying").upper()

    return OptionContract(
        underlying=TICKER_RENAMES.get(underlying, underlying),
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        multiplier=OPTION_ASSIGNMENT_SHARES,
    )



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
        option_contract = None
        if action in {
            ActionType.OPTION_GRANT,
            ActionType.OPTION_CLOSE,
            ActionType.OPTION_EXPIRY,
            ActionType.OPTION_ASSIGNMENT,
        }:
            if (
                symbol is None
                or (option_contract := parse_option_contract(symbol)) is None
            ):
                raise ParsingError(
                    file,
                    f"Cannot parse the option contract symbol {symbol!r} for "
                    f"Schwab action '{self.raw_action}'",
                )
            symbol = str(option_contract)
        elif symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row_dict[RequiredTransactionsColumn.DESCRIPTION.value]
        if (
            action == ActionType.DIVIDEND_TAX
            and symbol is None
            and "SCHWAB1 INT" in description
        ):
            # Withholding on account-level cash interest, not tied to a security.
            action = ActionType.INTEREST_TAX
        price = _parse_decimal(row_dict, RequiredTransactionsColumn.PRICE)
        quantity = _parse_decimal(row_dict, RequiredTransactionsColumn.QUANTITY)
        fees = _parse_decimal(
            row_dict, RequiredTransactionsColumn.FEES_AND_COMM
        ) or Decimal(0)
        amount = _parse_decimal(row_dict, RequiredTransactionsColumn.AMOUNT)

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
            option_contract=option_contract,
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
        or amount < 0
        or cash_merger.quantity is not None
        or cash_merger.price is not None
        or adjustment_quantity is None
        or adjustment_quantity >= 0
        or cash_merger_adj.amount is not None
        or cash_merger_adj.price is not None
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
        or amount < 0
        or full_redemption_adj.quantity is not None
        or full_redemption_adj.price is not None
        or redemption_quantity is None
        or redemption_quantity >= 0
        or full_redemption.price is not None
        or full_redemption.amount is not None
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
            # A directory load passes the directory, so name the file and line
            # the row records: "remove this row" needs to say which row.
            source = transaction.source
            raise ParsingError(
                source.file if source and source.file else file,
                f"Found a {transaction.raw_action} for {transaction.symbol} on "
                f"{transaction.date} with no Buy to match it in the "
                f"{CANCEL_BUY_SEARCH_DAYS} days before. The purchase may be "
                "missing from the export, older than that window, recorded "
                "with a different quantity or price, or already reversed by "
                "another cancellation. Remove this row; remove the purchase "
                "with it only if the purchase is still in the export and no "
                "other cancellation reverses it.",
                row_index=source.row if source else None,
            )

    if indices_to_remove:
        LOGGER.info(
            "Removed %d cancelled transaction(s) and their originals",
            len(indices_to_remove),
        )

    return [txn for i, txn in enumerate(transactions) if i not in indices_to_remove]


@dataclass(frozen=True)
class _Export:
    """One Schwab CSV from a directory, with the dates it actually covers."""

    rows: list[SchwabTransaction]
    file_path: Path

    @property
    def oldest(self) -> datetime.date:
        """Earliest transaction date in the file."""
        return min(row.date for row in self.rows)

    @property
    def newest(self) -> datetime.date:
        """Latest transaction date in the file."""
        return max(row.date for row in self.rows)


def _reject_overlapping_exports(exports: list[_Export]) -> None:
    """Refuse two exports whose transaction dates overlap.

    A Schwab CSV carries no transaction id, so a row repeated by an overlap is
    indistinguishable from a genuine repeat of the same trade: two identical
    buys of the same size and price on one day are a real thing. Continuing
    would report a disposal or acquisition that never happened, and would leave
    the order of a shared date decided by the filename.

    The comparison is over the dates present in each file rather than the range
    the user asked Schwab for. That is the stricter reading of what matters: if
    two requested ranges overlap but no transaction falls in the shared window,
    nothing is duplicated and there is nothing to refuse.
    """
    ordered = sorted(exports, key=lambda export: (export.oldest, export.newest))
    for earlier, later in itertools.pairwise(ordered):
        if later.oldest <= earlier.newest:
            raise ParsingError(
                later.file_path,
                f"has transactions from {later.oldest} to {later.newest}, "
                f"overlapping {earlier.file_path.name} ({earlier.oldest} to "
                f"{earlier.newest}). A Schwab CSV has no transaction id, so "
                "cgt-calc cannot tell a row repeated by the overlap from a "
                "genuine repeat of the same trade, and would count both. "
                "Re-export the ranges so they do not overlap, or remove the "
                "redundant file. If these are exports from two different "
                "Schwab accounts, cgt-calc cannot combine them: the CSV does "
                "not say which account a row belongs to.",
            )


def _row_file(transaction: SchwabTransaction) -> Path:
    """Return the file one row was read from, for pointing an error at it."""
    source = transaction.source
    if source is None or source.file is None:
        raise TypeError("Schwab row has no stamped source file")
    return source.file




@dataclass
class _WrittenOptionLot:
    """Unsettled quantity from one Sell to Open row."""

    transaction: SchwabTransaction
    remaining: Decimal
    assigned: Decimal = Decimal(0)


@dataclass
class _OptionAssignment:
    """An assignment row and the grants whose contracts it consumed."""

    transaction: SchwabTransaction
    allocations: list[tuple[_WrittenOptionLot, Decimal]]


def _option_quantity(transaction: SchwabTransaction, file: Path) -> Decimal:
    """Return and validate a Schwab option contract count."""
    quantity = transaction.quantity
    if (
        quantity is None
        or not quantity.is_finite()
        or quantity <= 0
        or quantity != quantity.to_integral_value()
    ):
        raise ParsingError(
            file,
            f"Invalid contract quantity for {transaction.raw_action} of "
            f"{transaction.symbol} on {transaction.date}: {quantity}",
        )
    return quantity


def _validate_written_option_row(
    transaction: SchwabTransaction,
    file: Path,
) -> None:
    """Validate the cash fields used to reconcile a written option."""
    quantity = _option_quantity(transaction, file)
    amount = transaction.amount
    price = transaction.price
    fees = transaction.fees
    if not fees.is_finite() or fees < 0:
        raise ParsingError(
            file,
            f"Invalid fees for {transaction.raw_action} of {transaction.symbol} "
            f"on {transaction.date}: {fees}",
        )

    if transaction.action is ActionType.OPTION_GRANT:
        valid = (
            amount is not None
            and amount.is_finite()
            and amount >= 0
            and price is not None
            and price.is_finite()
            and price >= 0
        )
        sign_description = "non-negative premium proceeds"
    elif transaction.action is ActionType.OPTION_CLOSE:
        valid = (
            amount is not None
            and amount.is_finite()
            and amount <= 0
            and price is not None
            and price.is_finite()
            and price >= 0
        )
        sign_description = "non-positive closing cost"
    else:
        valid = (amount is None or amount == 0) and price is None and fees == 0
        sign_description = "no price, fees or cash amount"

    if not valid:
        raise ParsingError(
            file,
            f"Invalid {transaction.raw_action} row for {transaction.symbol} on "
            f"{transaction.date}: expected {sign_description} and a positive "
            f"whole contract quantity, got quantity {quantity}, price {price}, "
            f"fees {fees}, amount {amount}",
        )

    if transaction.action in {ActionType.OPTION_GRANT, ActionType.OPTION_CLOSE}:
        assert amount is not None
        assert price is not None
        contract = transaction.option_contract
        assert contract is not None
        gross_premium = quantity * contract.multiplier * price
        expected_amount = (
            gross_premium - fees
            if transaction.action is ActionType.OPTION_GRANT
            else -(gross_premium + fees)
        )
        implied_price = (
            (amount + fees) / (quantity * contract.multiplier)
            if transaction.action is ActionType.OPTION_GRANT
            else (-amount - fees) / (quantity * contract.multiplier)
        )
        if not approx_equal(amount, expected_amount) and abs(
            implied_price - price
        ) >= Decimal("0.0001"):
            raise ParsingError(
                file,
                f"Option premium does not match quantity, price and fees for "
                f"{transaction.raw_action} of {transaction.symbol} on "
                f"{transaction.date}: expected {expected_amount}, got {amount}",
            )


def _allocate_option_outcome(
    outcome: SchwabTransaction,
    open_lots: dict[OptionContract, list[_WrittenOptionLot]],
    file: Path,
) -> list[tuple[_WrittenOptionLot, Decimal]]:
    """Allocate a close, expiry or assignment to earlier grants FIFO."""
    contract = outcome.option_contract
    assert contract is not None
    needed = _option_quantity(outcome, file)
    allocations: list[tuple[_WrittenOptionLot, Decimal]] = []
    for lot in open_lots.get(contract, []):
        if lot.transaction.date > outcome.date or lot.remaining == 0:
            continue
        quantity = min(needed, lot.remaining)
        allocations.append((lot, quantity))
        lot.remaining -= quantity
        needed -= quantity
        if needed == 0:
            break

    if needed:
        raise ParsingError(
            file,
            f"Schwab reports {outcome.raw_action} of {outcome.quantity} contract(s) "
            f"for {contract} on {outcome.date}, but only "
            f"{_option_quantity(outcome, file) - needed} earlier written "
            "contract(s) are available. Include the missing Sell to Open rows "
            "in the export.",
        )
    return allocations


def _assignment_adjustments(
    allocations: list[tuple[_WrittenOptionLot, Decimal]],
) -> list[CapitalAdjustment]:
    """Return grant premiums to fold into an assigned share transaction."""
    adjustments = []
    for lot, quantity in allocations:
        opening = lot.transaction
        opening_quantity = opening.quantity
        assert opening_quantity is not None
        amount = opening.amount
        assert amount is not None
        proportion = quantity / opening_quantity
        adjustments.append(
            CapitalAdjustment(
                date=opening.date,
                net_amount=amount * proportion,
                fees=opening.fees * proportion,
                currency=opening.currency,
            )
        )
    return adjustments


def _find_assignment_share_transaction(
    transactions: list[SchwabTransaction],
    assignment: SchwabTransaction,
    assigned_shares: Decimal,
    used: set[int],
    file: Path,
) -> SchwabTransaction | None:
    """Find Schwab's separate stock row created by an option assignment."""
    contract = assignment.option_contract
    assert contract is not None
    expected_action = (
        ActionType.SELL if contract.option_type is OptionType.CALL else ActionType.BUY
    )
    candidates = [
        transaction
        for transaction in transactions
        if id(transaction) not in used
        and transaction.option_contract is None
        and transaction.action is expected_action
        and transaction.symbol == contract.underlying
        and transaction.quantity == assigned_shares
        and transaction.price == contract.strike
        and assignment.date
        <= transaction.date
        <= assignment.date + datetime.timedelta(days=OPTION_ASSIGNMENT_MATCH_DAYS)
    ]
    if len(candidates) > 1:
        raise ParsingError(
            file,
            f"Cannot identify which {contract.underlying} stock transaction "
            f"belongs to the assignment of {contract} on {assignment.date}",
        )
    return candidates[0] if candidates else None


def _apply_option_assignment(
    transactions: list[SchwabTransaction],
    assignment: _OptionAssignment,
    used_share_transactions: set[int],
    file: Path,
) -> BrokerTransaction | None:
    """Fold written-option premium into the assigned underlying shares."""
    transaction = assignment.transaction
    contract = transaction.option_contract
    assert contract is not None
    contracts = sum((quantity for _, quantity in assignment.allocations), Decimal(0))
    assigned_shares = contracts * contract.multiplier
    share_transaction = _find_assignment_share_transaction(
        transactions,
        transaction,
        assigned_shares,
        used_share_transactions,
        file,
    )
    adjustments = _assignment_adjustments(assignment.allocations)

    if share_transaction is not None:
        share_transaction.capital_adjustments.extend(adjustments)
        used_share_transactions.add(id(share_transaction))
        return None

    fees = transaction.fees
    gross_amount = assigned_shares * contract.strike
    if contract.option_type is OptionType.CALL:
        action = ActionType.SELL
        amount = gross_amount - fees
    else:
        action = ActionType.BUY
        amount = -(gross_amount + fees)
    return BrokerTransaction(
        date=transaction.date,
        action=action,
        symbol=contract.underlying,
        description=f"Shares delivered on assignment of {contract}",
        quantity=assigned_shares,
        price=contract.strike,
        fees=fees,
        amount=amount,
        currency=transaction.currency,
        broker=transaction.broker,
        capital_adjustments=adjustments,
    )


def _reconcile_written_options(
    transactions: list[SchwabTransaction],
) -> list[BrokerTransaction]:
    """Resolve Schwab written-option lifecycles for UK capital gains."""
    open_lots: dict[OptionContract, list[_WrittenOptionLot]] = defaultdict(list)
    assignments: dict[tuple[datetime.date, OptionContract], _OptionAssignment] = {}

    for transaction in transactions:
        if transaction.option_contract is None:
            continue
        _validate_written_option_row(transaction, _row_file(transaction))
        if transaction.action is ActionType.OPTION_GRANT:
            quantity = _option_quantity(transaction, _row_file(transaction))
            transaction.written_option_tax = WrittenOptionTaxData(quantity)
            open_lots[transaction.option_contract].append(
                _WrittenOptionLot(transaction, quantity)
            )
            continue

        allocations = _allocate_option_outcome(
            transaction, open_lots, _row_file(transaction)
        )
        if transaction.action is ActionType.OPTION_CLOSE:
            amount = transaction.amount
            assert amount is not None
            total_cost = -amount
            allocated_cost = Decimal(0)
            total_quantity = _option_quantity(
                transaction, _row_file(transaction)
            )
            for index, (lot, quantity) in enumerate(allocations):
                cost = (
                    total_cost - allocated_cost
                    if index == len(allocations) - 1
                    else total_cost * quantity / total_quantity
                )
                allocated_cost += cost
                tax_data = lot.transaction.written_option_tax
                assert tax_data is not None
                tax_data.close_costs.append(
                    DatedAmount(transaction.date, cost, transaction.currency)
                )
        elif transaction.action is ActionType.OPTION_ASSIGNMENT:
            for lot, quantity in allocations:
                lot.assigned += quantity
            contract = transaction.option_contract
            assert contract is not None
            key = (transaction.date, contract)
            if key in assignments:
                assignments[key].allocations.extend(allocations)
            else:
                assignments[key] = _OptionAssignment(transaction, allocations)

    for lots in open_lots.values():
        for lot in lots:
            tax_data = lot.transaction.written_option_tax
            assert tax_data is not None
            tax_data.taxable_quantity -= lot.assigned

    used_share_transactions: set[int] = set()
    synthetic_share_transactions = [
        synthetic
        for assignment in assignments.values()
        if (
            synthetic := _apply_option_assignment(
                transactions,
                assignment,
                used_share_transactions,
                _row_file(assignment.transaction),
            )
        )
        is not None
    ]
    reconciled: list[BrokerTransaction] = [
        transaction
        for transaction in transactions
        if transaction.action
        not in {ActionType.OPTION_EXPIRY, ActionType.OPTION_ASSIGNMENT}
    ]
    reconciled.extend(synthetic_share_transactions)
    return reconciled


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
        price_str = row_dict[fair_market_value_price_header]
        try:
            price = (
                parse_decimal(
                    price_str, f"column '{fair_market_value_price_header}'", strip="$,"
                )
                if price_str != ""
                else None
            )
        except ValueError as err:
            # This file is read outside StandardCSVParser, so nothing else
            # turns the helper's ValueError into an error naming the row.
            # The format splits each award over two rows and states the price
            # on the lower one, which is the row the reader has to correct.
            raise ParsingError(
                schwab_award_transactions_file, str(err), row_index=row_index + 1
            ) from err
        if symbol is not None and price is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
            initial_prices[date][symbol] = price
    return AwardPrices(award_prices=dict(initial_prices))


class SchwabParser(BaseSingleFileParser[BrokerTransaction]):
    """Parser for Charles Schwab transaction files."""

    arg_name = "schwab"
    pretty_name = "Charles Schwab"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--schwab"]
    # Schwab cannot inherit BaseDirParser, which would rename --schwab-file to
    # --schwab-dir, so it declares the same directory conventions by hand and
    # reads them through cls in load_from_dir.
    glob_dir: ClassVar[str] = "*.csv"

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
        # Schwab keeps both forms: `--schwab-file` predates the directory
        # support, so removing it would break every existing command. The
        # other directory brokers replaced their file flag instead.
        set_completer(
            arg_group.add_argument(
                "--schwab-dir",
                type=existing_directory_type,
                default=None,
                metavar="DIR",
                help="directory with Charles Schwab transaction history in CSV format",
            ),
            shtab.DIRECTORY,
        )
        super().register_arguments(arg_group)

    @classmethod
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        if args.schwab_dir and args.schwab_file:
            # main() rejects this through argparse, for the usage message and
            # exit code 2. Repeated here because this is the layer that knows
            # both flags: taking the directory branch silently would drop
            # every transaction in the file. Checked before any I/O so a bad
            # award file can't mask the real problem.
            raise CgtError(
                "--schwab-file and --schwab-dir cannot be used together. Pass "
                "the directory holding every export, or the single file."
            )
        award_path = args.schwab_award_file
        cls.awards_prices = _read_schwab_awards(award_path)
        if args.schwab_dir:
            # list is invariant, so widen explicitly rather than return list[T].
            transactions: list[BrokerTransaction] = list(
                cls.load_from_dir(args.schwab_dir)
            )
            return transactions
        return super().load_from_args(args)

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read Schwab transactions from file."""
        transactions = cls._read_rows(file, file_path)
        transactions = _unify_schwab_paired_transactions(transactions, file_path)
        transactions = _filter_cancelled_buy_transactions(transactions, file_path)
        transactions.reverse()
        return list(transactions)

    @classmethod
    @override
    def finalize_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Reconcile option lifecycles once the whole account is read.

        An opening row and its outcome can sit in different files of one
        `--schwab-dir` export, so this cannot run per file. The boundary hook
        runs exactly once, after every file has been read and stamped.
        """
        return _reconcile_written_options(
            [row for row in transactions if isinstance(row, SchwabTransaction)]
        )

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> list[BrokerTransaction]:
        """Read every CSV in the directory as one export.

        Schwab limits how much history one export can cover, so a long history
        arrives as several date-range files. They are parsed separately and
        then treated as a single export.

        Corporate-action pairing stays per file: both shapes it recognises are
        same-date by construction, and refusing overlapping exports first
        guarantees one date lives in exactly one file, so a pair can never be
        split. Cancellation matching is the opposite: the Buy a Cancel Buy
        reverses is up to CANCEL_BUY_SEARCH_DAYS away, so it routinely sits in
        the neighbouring file and can only be found once everything is merged.
        """
        # One directory is one declared account boundary, as it is for the
        # other directory brokers, so every file in it shares a token.
        account = next_account_token(cls.pretty_name)
        exports: list[_Export] = []
        for file_path in sorted(dir_path.glob(cls.glob_dir, case_sensitive=False)):
            if not cls.file_path_filter(file_path):
                continue
            try:
                with file_path.open(encoding=cls.encoding) as file:
                    parsing_msg(file_path)
                    rows = cls._read_rows(file, file_path, from_directory=True)
            except OSError as exc:
                # Branch on what the path is, not on the exception type:
                # opening a directory raises IsADirectoryError on POSIX but
                # PermissionError on Windows.
                if file_path.is_dir():
                    raise ParsingError(
                        file_path,
                        "is not a regular file. Remove or move it out of the "
                        "Schwab directory.",
                    ) from exc
                raise ParsingError(
                    file_path,
                    f"could not be read ({exc.strerror or exc}). Remove or move "
                    "it out of the Schwab directory.",
                ) from exc
            if rows:
                exports.append(_Export(rows=rows, file_path=file_path))

        # Before pairing, not after: an overlap is exactly what can put the two
        # halves of a corporate action in different files, and pairing would
        # then report a missing half rather than the overlap behind it. Pairing
        # only ever merges rows sharing a date, so it cannot change the span a
        # file covers and the answer here is the same either way.
        _reject_overlapping_exports(exports)

        exports = [
            replace(
                export,
                rows=_unify_schwab_paired_transactions(export.rows, export.file_path),
            )
            for export in exports
        ]
        for export in exports:
            # Rows are still newest-first here, so stamp a reversed view: index
            # has to rise with time, as it does on the single-file path where
            # stamping happens after the reverse.
            cls.stamp_source(list(reversed(export.rows)), export.file_path, account)

        # Every export is newest-first and no date spans two of them, so
        # ordering whole exports by their newest date makes the concatenation
        # newest-first without disturbing row order inside a file. Sorting the
        # rows themselves would have to break ties between files, and the only
        # tiebreak available is the filename.
        exports.sort(key=lambda export: export.newest, reverse=True)

        transactions = [row for export in exports for row in export.rows]
        transactions = _filter_cancelled_buy_transactions(transactions, dir_path)
        transactions.reverse()

        # Checked after filtering, not before: a directory whose only rows are
        # a Buy and the Cancel Buy that reverses it ends up empty, and has to
        # warn like any other empty result.
        if not transactions:
            LOGGER.warning(
                "No transactions detected in directory %s for broker %s",
                dir_path,
                cls.pretty_name,
            )
        # A directory is one account boundary and does not go through
        # load_from_file, so it finalizes here instead.
        parsed_transactions: list[BrokerTransaction] = list(transactions)
        return cls.finalize_transactions(
            cls.post_process_transactions(parsed_transactions)
        )

    @classmethod
    def _directory_help(cls, file_path: Path) -> str:
        return (
            f" Every {cls.glob_dir} in the directory is parsed as Schwab "
            f"transaction history; remove or move this file ({file_path.name}) "
            "out of the directory."
        )

    @classmethod
    def _read_rows(
        cls, file: TextIO, file_path: Path, *, from_directory: bool = False
    ) -> list[SchwabTransaction]:
        """Parse the rows of one Schwab CSV, without any cross-row pass.

        A ``classmethod`` because pricing a vest needs ``cls.awards_prices``,
        which ``load_from_args`` fills in from ``--schwab-award-file``.
        """
        lines = list(csv.reader(file))
        if not lines:
            message = "Charles Schwab transactions CSV file is empty."
            if from_directory:
                message += cls._directory_help(file_path)
            raise ParsingError(file_path, message)
        header = lines[0]

        required_headers = {column.value for column in RequiredTransactionsColumn}
        if not required_headers.issubset(header):
            directory_help = ""
            if from_directory:
                directory_help = cls._directory_help(file_path)
                award_headers = {column.value for column in RequiredAwardColumn}
                if award_headers.issubset(header):
                    directory_help += " Pass it with --schwab-award-file instead."
            raise ParsingError(
                file_path,
                "Missing columns in Schwab transaction file: "
                f"{required_headers.difference(header)}.{directory_help}",
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

            transaction.source = TransactionSource(row=index)
            transactions.append(transaction)
        return transactions
