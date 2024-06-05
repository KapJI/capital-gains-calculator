"""Trading 212 parser."""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

COLUMNS: Final[list[str]] = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result (GBP)",
    "Result",
    "Currency (Result)",
    "Total (GBP)",
    "Total",
    "Currency (Total)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Charge amount (GBP)",
    "Transaction fee (GBP)",
    "Transaction fee",
    "Finra fee (GBP)",
    "Stamp duty (GBP)",
    "Notes",
    "ID",
    "Currency conversion fee (GBP)",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "Currency (Transaction fee)",
]


def decimal_or_none(val: str) -> Decimal | None:
    """Convert value to Decimal."""
    return Decimal(val) if val not in ["", "Not available"] else None


def action_from_str(label: str, filename: str) -> ActionType:
    """Convert label to ActionType."""
    if label in [
        "Market buy",
        "Limit buy",
    ]:
        return ActionType.BUY

    if label in [
        "Market sell",
        "Limit sell",
    ]:
        return ActionType.SELL

    if label in [
        "Deposit",
        "Withdrawal",
    ]:
        return ActionType.TRANSFER

    if label in [
        "Dividend (Ordinary)",
        "Dividend (Dividend)",
        "Dividend (Dividends paid by us corporations)",
    ]:
        return ActionType.DIVIDEND

    if label in ["Interest on cash"]:
        return ActionType.INTEREST

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    raise ParsingError(filename, f"Unknown action: {label}")


class Trading212Transaction(BrokerTransaction):
    """Represent single Trading 212 transaction."""

    def __init__(self, header: list[str], row_raw: list[str], filename: str):
        """Create transaction from CSV row."""
        row = dict(zip(header, row_raw))
        time_str = row["Time"]
        time_format = "%Y-%m-%d %H:%M:%S.%f" if "." in time_str else "%Y-%m-%d %H:%M:%S"
        self.datetime = datetime.strptime(time_str, time_format)
        date = self.datetime.date()
        self.raw_action = row["Action"]
        action = action_from_str(self.raw_action, filename)
        symbol = row["Ticker"] if row["Ticker"] != "" else None
        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row["Name"]
        quantity = decimal_or_none(row["No. of shares"])
        self.price_foreign = decimal_or_none(row["Price / share"])
        self.currency_foreign = row["Currency (Price / share)"]
        self.exchange_rate = decimal_or_none(row["Exchange rate"])
        self.transaction_fee = Decimal(row.get("Transaction fee (GBP)") or "0")
        transaction_fee_foreign = Decimal(row.get("Transaction fee") or "0")
        if transaction_fee_foreign > 0:
            if row.get("Currency (Transaction fee)") != "GBP":
                raise ParsingError(
                    filename,
                    "The transaction fee is not in GBP which is not supported yet",
                )
            self.transaction_fee += transaction_fee_foreign
        self.finra_fee = Decimal(row.get("Finra fee (GBP)") or "0")
        self.stamp_duty = Decimal(row.get("Stamp duty (GBP)") or "0")
        self.conversion_fee = Decimal(row.get("Currency conversion fee (GBP)") or "0")
        conversion_fee_foreign = Decimal(row.get("Currency conversion fee") or "0")
        if conversion_fee_foreign > 0:
            if row.get("Currency (Currency conversion fee)") != "GBP":
                raise ParsingError(
                    filename,
                    "The transaction fee is not in GBP which is not supported yet",
                )
            self.conversion_fee += conversion_fee_foreign
        fees = self.transaction_fee + self.finra_fee + self.conversion_fee
        if "Total" in row:
            amount = decimal_or_none(row["Total"])
            currency = row["Currency (Total)"]
        else:
            amount = decimal_or_none(row["Total (GBP)"])
            currency = "GBP"
        if (
            amount is not None
            and (action == ActionType.BUY or self.raw_action == "Withdrawal")
            and amount > 0
        ):
            amount *= -1
        price = (
            abs(amount + fees) / quantity
            if amount is not None and quantity is not None
            else None
        )
        if (
            price is not None
            and self.price_foreign is not None
            and (self.currency_foreign == "GBP" or self.exchange_rate is not None)
        ):
            calculated_price_foreign = price * (self.exchange_rate or Decimal("1"))
            discrepancy = self.price_foreign - calculated_price_foreign
            if abs(discrepancy) > Decimal("0.015"):
                print(
                    "WARNING: The Price / share for this transaction "
                    "after converting and adding in the fees "
                    f"doesn't add up to the total amount: {row}. "
                    "You may fix the csv by looking at the transaction "
                    f"in the UI. Discrepancy / share: {discrepancy:.3f}."
                )

        self.isin = row["ISIN"]
        self.transaction_id = row["ID"] if "ID" in row else None
        self.notes = row["Notes"] if "Notes" in row else None
        broker = "Trading212"
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

    def __hash__(self) -> int:
        """Calculate hash."""
        return hash(self.transaction_id)


def validate_header(header: list[str], filename: str) -> None:
    """Check if header is valid."""
    for actual in header:
        if actual not in COLUMNS:
            msg = f"Unknown column {actual}"
            raise ParsingError(filename, msg)


def by_date_and_action(transaction: Trading212Transaction) -> tuple[datetime, bool]:
    """Sort by date and action type."""

    # If there's a deposit in the same second as a buy
    # (happens with the referral award at least)
    # we want to put the buy last to avoid negative balance errors
    return (transaction.datetime, transaction.action == ActionType.BUY)


def read_trading212_transactions(transactions_folder: str) -> list[BrokerTransaction]:
    """Parse Trading 212 transactions from CSV file."""
    transactions = []
    for file in Path(transactions_folder).glob("*.csv"):
        with Path(file).open(encoding="utf-8") as csv_file:
            print(f"Parsing {file}")
            lines = list(csv.reader(csv_file))
            header = lines[0]
            validate_header(header, str(file))
            lines = lines[1:]
            cur_transactions = [
                Trading212Transaction(header, row, str(file)) for row in lines
            ]
            if len(cur_transactions) == 0:
                print(f"WARNING: no transactions detected in file {file}")
            transactions += cur_transactions
    # remove duplicates
    transactions = list(set(transactions))
    transactions.sort(key=by_date_and_action)
    return list(transactions)
