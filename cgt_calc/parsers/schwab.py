"""Charles Schwab parser."""
from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
import datetime
from decimal import Decimal
import itertools
from pathlib import Path

from cgt_calc.exceptions import (
    ExchangeRateMissingError,
    ParsingError,
    SymbolMissingError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
)
from cgt_calc.model import ActionType, BrokerTransaction


@dataclass
class AwardPrices:
    """Class to store initial stock prices."""

    award_prices: dict[datetime.date, dict[str, Decimal]]

    def get(self, date: datetime.date, symbol: str) -> Decimal:
        """Get initial stock price at given date."""
        # Award dates may go back for few days, depending on
        # holidays or weekends, so we do a linear search
        # in the past to find the award price
        for i in range(7):
            to_search = date - datetime.timedelta(days=i)

            if (
                to_search in self.award_prices
                and symbol in self.award_prices[to_search]
            ):
                return self.award_prices[to_search][symbol]
        raise ExchangeRateMissingError(symbol, date)


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
        "Funds Received",
        "Journal",
        "Cash In Lieu",
    ]:
        return ActionType.TRANSFER

    if label == "Stock Plan Activity":
        return ActionType.STOCK_ACTIVITY

    if label in ["Qualified Dividend", "Cash Dividend"]:
        return ActionType.DIVIDEND

    if label in ["NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"]:
        return ActionType.TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in ["Adjustment", "IRS Withhold Adj"]:
        return ActionType.ADJUSTMENT

    if label in ["Short Term Cap Gain", "Long Term Cap Gain"]:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label == "Credit Interest":
        return ActionType.INTEREST

    raise ParsingError("schwab transactions", f"Unknown action: {label}")


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(
        self,
        row: list[str],
        file: str,
    ):
        """Create transaction from CSV row."""
        if len(row) != 9:
            raise UnexpectedColumnCountError(row, 9, file)
        if row[8] != "":
            raise ParsingError(file, "Column 9 should be empty")
        as_of_str = " as of "
        if as_of_str in row[0]:
            index = row[0].find(as_of_str) + len(as_of_str)
            date_str = row[0][index:]
        else:
            date_str = row[0]
        date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        self.raw_action = row[1]
        action = action_from_str(self.raw_action)
        symbol = row[2] if row[2] != "" else None
        description = row[3]
        quantity = Decimal(row[4]) if row[4] != "" else None
        price = Decimal(row[5].replace("$", "")) if row[5] != "" else None
        fees = Decimal(row[6].replace("$", "")) if row[6] != "" else Decimal(0)
        amount = Decimal(row[7].replace("$", "")) if row[7] != "" else None

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
        row: list[str], file: str, awards_prices: AwardPrices
    ) -> SchwabTransaction:
        """Create and post process a SchwabTransaction."""
        transaction = SchwabTransaction(row, file)
        if (
            transaction.price is None
            and transaction.action == ActionType.STOCK_ACTIVITY
        ):
            symbol = transaction.symbol
            if symbol is None:
                raise SymbolMissingError(transaction)
            transaction.price = awards_prices.get(transaction.date, symbol)
        return transaction


def read_schwab_transactions(
    transactions_file: str, schwab_award_transactions_file: str | None
) -> list[BrokerTransaction]:
    """Read Schwab transactions from file."""
    awards_prices = _read_schwab_awards(schwab_award_transactions_file)
    try:
        with Path(transactions_file).open(encoding="utf-8") as csv_file:
            lines = list(csv.reader(csv_file))
            # Remove headers and footer
            lines = lines[2:-1]
            transactions = [
                SchwabTransaction.create(row, transactions_file, awards_prices)
                for row in lines
            ]
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

    lines = []
    if schwab_award_transactions_file is not None:
        try:
            with Path(schwab_award_transactions_file).open(
                encoding="utf-8"
            ) as csv_file:
                lines = list(csv.reader(csv_file))
                # Remove headers
                lines = lines[2:]
        except FileNotFoundError:
            print(
                "WARNING: Couldn't locate Schwab award "
                f"file({schwab_award_transactions_file})"
            )
    else:
        print("WARNING: No schwab award file provided")

    modulo = len(lines) % 3
    if modulo != 0:
        raise UnexpectedRowCountError(
            len(lines) - modulo + 3, schwab_award_transactions_file or ""
        )

    for row in zip(lines[::3], lines[1::3], lines[2::3]):
        if len(row) != 3:
            raise UnexpectedColumnCountError(
                list(itertools.chain(*row)), 3, schwab_award_transactions_file or ""
            )

        lapse_main, _, lapse_data = row

        if len(lapse_main) != 8:
            raise UnexpectedColumnCountError(
                lapse_main, 8, schwab_award_transactions_file or ""
            )
        if len(lapse_data) != 8:
            raise UnexpectedColumnCountError(
                lapse_data, 7, schwab_award_transactions_file or ""
            )

        date_str = lapse_main[0]
        date = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        symbol = lapse_main[2] if lapse_main[2] != "" else None
        price = Decimal(lapse_data[3].replace("$", "")) if lapse_data[3] != "" else None
        if symbol is not None and price is not None:
            initial_prices[date][symbol] = price
    return AwardPrices(award_prices=dict(initial_prices))
