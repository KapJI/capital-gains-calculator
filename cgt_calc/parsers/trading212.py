import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction

columns = [
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
    "Total (GBP)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Charge amount (GBP)",
    "Transaction fee (GBP)",
    "Finra fee (GBP)",
    "Notes",
    "ID",
]


def decimal_or_none(val: str) -> Optional[Decimal]:
    return Decimal(val) if val not in ["", "Not available"] else None


def action_from_str(label: str, filename: str) -> ActionType:
    if label in [
        "Market buy",
        "Limit buy",
    ]:
        return ActionType.BUY
    elif label in [
        "Market sell",
        "Limit sell",
    ]:
        return ActionType.SELL
    elif label in [
        "Deposit",
        "Withdrawal",
    ]:
        return ActionType.TRANSFER
    elif label in ["Dividend (Ordinary)"]:
        return ActionType.DIVIDEND
    else:
        raise ParsingError(filename, f"Unknown action: {label}")


class Trading212Transaction(BrokerTransaction):
    def __init__(self, row_ints: List[str], filename: str):
        if len(columns) != len(row_ints):
            raise UnexpectedColumnCountError(len(columns), row_ints, filename)
        row = {col: row_ints[i] for i, col in enumerate(columns)}
        time_str = row["Time"]
        self.datetime = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        date = self.datetime.date()
        self.raw_action = row["Action"]
        action = action_from_str(self.raw_action, filename)
        symbol = row["Ticker"] if row["Ticker"] != "" else None
        description = row["Name"]
        quantity = decimal_or_none(row["No. of shares"])
        self.price_foreign = decimal_or_none(row["Price / share"])
        self.currency_foreign = row["Currency (Price / share)"]
        self.exchange_rate = decimal_or_none(row["Exchange rate"])
        self.transaction_fee = decimal_or_none(row["Transaction fee (GBP)"])
        self.finra_fee = decimal_or_none(row["Finra fee (GBP)"])
        fees = (self.transaction_fee or Decimal(0)) + (self.finra_fee or Decimal(0))
        amount = decimal_or_none(row["Total (GBP)"])
        price = (
            abs(amount / quantity)
            if amount is not None and quantity is not None
            else None
        )
        if amount is not None:
            if action == ActionType.BUY or self.raw_action == "Withdrawal":
                amount *= -1
            amount -= fees
        self.isin = row["ISIN"]
        self.id = row["ID"]
        self.notes = row["Notes"]
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
            "GBP",
            broker,
        )

    def __eq__(self, other):
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)


def validate_header(header: List[str], filename: str):
    if len(columns) != len(header):
        raise UnexpectedColumnCountError(len(columns), header, filename)
    for i, (expected, actual) in enumerate(zip(columns, header)):
        if expected != actual:
            msg = f"Expected column {i+1} to be {expected} but found {actual}"
            raise ParsingError(msg, filename)


# if there's a deposit in the same second as a buy
# (happens with the referral award at least)
# we want to put the buy last to avoid negative balance errors
def by_date_and_action(transaction: Trading212Transaction) -> Tuple[datetime, bool]:
    return (transaction.datetime, transaction.action == ActionType.BUY)


def read_trading212_transactions(transactions_folder: str) -> List[BrokerTransaction]:
    transactions = []
    for file in Path(transactions_folder).glob("*.csv"):
        with open(file) as csv_file:
            print(f"Parsing {file}")
            lines = [line for line in csv.reader(csv_file)]
            validate_header(lines[0], str(file))
            lines = lines[1:]
            cur_transactions = [Trading212Transaction(row, str(file)) for row in lines]
            if len(cur_transactions) == 0:
                print(f"WARNING: no transactions detected in file {file}")
            transactions += cur_transactions
    # remove duplicates
    transactions = list(set(transactions))
    transactions.sort(key=by_date_and_action)
    return list(transactions)
