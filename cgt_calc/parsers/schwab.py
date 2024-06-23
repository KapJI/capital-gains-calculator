"""Charles Schwab parser."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import (
    ParsingError,
    SymbolMissingError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
)
from cgt_calc.model import ActionType, BrokerTransaction


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


def action_from_str(label: str) -> ActionType:
    """Convert string label to ActionType."""
    if label == "Buy":
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
        return ActionType.TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in ["Adjustment", "IRS Withhold Adj", "Wire Funds Adj"]:
        return ActionType.ADJUSTMENT

    if label in ["Short Term Cap Gain", "Long Term Cap Gain"]:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label == "Credit Interest":
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

    raise ParsingError("schwab transactions", f"Unknown action: {label}")


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(
        self,
        row_dict: OrderedDict[str, str],
        file: str,
    ):
        """Create transaction from CSV row."""
        if len(row_dict) < 8 or len(row_dict) > 9:
            # Old transactions had empty 9th column.
            raise UnexpectedColumnCountError(list(row_dict.values()), 8, file)
        if len(row_dict) == 9 and list(row_dict.values())[8] != "":
            raise ParsingError(file, "Column 9 should be empty")
        as_of_str = " as of "
        date_header = SchwabTransactionsFileRequiredHeaders.DATE.value
        if as_of_str in row_dict[date_header]:
            index = row_dict[date_header].find(as_of_str) + len(as_of_str)
            date_str = row_dict[date_header][index:]
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
        action = action_from_str(self.raw_action)
        symbol_header = SchwabTransactionsFileRequiredHeaders.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description_header = SchwabTransactionsFileRequiredHeaders.DESCRIPTION.value
        description = row_dict[description_header]
        price_header = SchwabTransactionsFileRequiredHeaders.PRICE.value
        price = (
            Decimal(row_dict[price_header].replace("$", ""))
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
        row_dict: OrderedDict[str, str], file: str, awards_prices: AwardPrices
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
            transaction.date, transaction.price = awards_prices.get(
                transaction.date, symbol
            )
        return transaction


def _unify_schwab_cash_merger_trxs(
    transactions: list[SchwabTransaction],
) -> list[SchwabTransaction]:
    filtered: list[SchwabTransaction] = []
    for transaction in transactions:
        if transaction.raw_action == "Cash Merger Adj":
            assert (
                len(filtered) > 0
            ), "Cash Merger Adj must be precedeed by a Cash Merger transaction"
            assert filtered[-1].raw_action == "Cash Merger"
            assert filtered[-1].description == transaction.description
            assert filtered[-1].symbol == transaction.symbol
            assert filtered[-1].date == transaction.date
            assert filtered[-1].quantity is None
            assert filtered[-1].price is None
            assert filtered[-1].amount is not None
            assert transaction.amount is None
            assert transaction.quantity is not None
            # the quantity is negative but
            # because we store it as a 'sell' we need it positive
            filtered[-1].quantity = -1 * transaction.quantity
            filtered[-1].price = filtered[-1].amount / filtered[-1].quantity
            filtered[-1].fees += transaction.fees
            print(
                "WARNING: Cash Merger support is not complete and doesn't cover the "
                "cases when shares are received aside from cash,  "
                "please review this transaction carefully: "
                f"{filtered[-1]}"
            )
        else:
            filtered.append(transaction)
    return filtered


def read_schwab_transactions(
    transactions_file: str, schwab_award_transactions_file: str | None
) -> list[BrokerTransaction]:
    """Read Schwab transactions from file."""
    awards_prices = _read_schwab_awards(schwab_award_transactions_file)
    try:
        with Path(transactions_file).open(encoding="utf-8") as csv_file:
            lines = list(csv.reader(csv_file))
            headers = lines[0]

            required_headers = set(
                {header.value for header in SchwabTransactionsFileRequiredHeaders}
            )
            if not required_headers.issubset(headers):
                raise ParsingError(
                    transactions_file,
                    "Missing columns in Schwab transaction file: "
                    f"{required_headers.difference(headers)}",
                )

            # Remove header
            lines = lines[1:]
            transactions = [
                SchwabTransaction.create(
                    OrderedDict(zip(headers, row)), transactions_file, awards_prices
                )
                for row in lines
                if any(row)
            ]
            transactions = _unify_schwab_cash_merger_trxs(transactions)
            transactions.reverse()
            return list(transactions)
    except FileNotFoundError:
        print(f"WARNING: Couldn't locate Schwab transactions file({transactions_file})")
        return []


def _read_schwab_awards(
    schwab_award_transactions_file: str | None,
) -> AwardPrices:
    """Read initial stock prices from CSV file."""
    initial_prices: dict[datetime.date, dict[str, Decimal]] = defaultdict(dict)

    headers = []

    lines = []
    if schwab_award_transactions_file is not None:
        try:
            with Path(schwab_award_transactions_file).open(
                encoding="utf-8"
            ) as csv_file:
                lines = list(csv.reader(csv_file))
                headers = lines[0]
                required_headers = set(
                    {header.value for header in AwardsTransactionsFileRequiredHeaders}
                )
                if not required_headers.issubset(headers):
                    raise ParsingError(
                        schwab_award_transactions_file,
                        "Missing columns in awards file: "
                        f"{required_headers.difference(headers)}",
                    )

                # Remove headers
                lines = lines[1:]
        except FileNotFoundError:
            print(
                "WARNING: Couldn't locate Schwab award "
                f"file({schwab_award_transactions_file})"
            )
    else:
        print("WARNING: No schwab award file provided")

    modulo = len(lines) % 2
    if modulo != 0:
        raise UnexpectedRowCountError(
            len(lines) - modulo + 2, schwab_award_transactions_file or ""
        )

    for upper_row, lower_row in zip(lines[::2], lines[1::2]):
        # in this format each row is split into two rows,
        # so we combine them safely below
        row = []
        for upper_col, lower_col in zip(upper_row, lower_row):
            assert upper_col == "" or lower_col == ""
            row.append(upper_col + lower_col)

        if len(row) != len(headers):
            raise UnexpectedColumnCountError(
                row, len(headers), schwab_award_transactions_file or ""
            )

        row_dict = OrderedDict(zip(headers, row))
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
