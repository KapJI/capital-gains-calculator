import csv
import datetime
from decimal import Decimal
from typing import List

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType, BrokerTransaction


def action_from_str(label: str) -> ActionType:
    if label == "Buy":
        return ActionType.BUY
    elif label == "Sell":
        return ActionType.SELL
    elif label in [
        "MoneyLink Transfer",
        "Misc Cash Entry",
        "Service Fee",
        "Wire Funds",
        "Funds Received",
        "Journal",
        "Cash In Lieu",
    ]:
        return ActionType.TRANSFER
    elif label == "Stock Plan Activity":
        return ActionType.STOCK_ACTIVITY
    elif label in ["Qualified Dividend", "Cash Dividend"]:
        return ActionType.DIVIDEND
    elif label in ["NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"]:
        return ActionType.TAX
    elif label == "ADR Mgmt Fee":
        return ActionType.FEE
    elif label in ["Adjustment", "IRS Withhold Adj"]:
        return ActionType.ADJUSTMENT
    elif label in ["Short Term Cap Gain", "Long Term Cap Gain"]:
        return ActionType.CAPITAL_GAIN
    elif label == "Spin-off":
        return ActionType.SPIN_OFF
    elif label == "Credit Interest":
        return ActionType.INTEREST
    else:
        raise ParsingError("schwab transactions", f"Unknown action: {label}")


class SchwabTransaction(BrokerTransaction):
    def __init__(self, row: List[str], file: str):
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


def read_schwab_transactions(transactions_file: str) -> List[BrokerTransaction]:
    try:
        with open(transactions_file) as csv_file:
            lines = [line for line in csv.reader(csv_file)]
            lines = lines[2:-1]
            transactions = [SchwabTransaction(row, transactions_file) for row in lines]
            transactions.reverse()
            return list(transactions)
    except FileNotFoundError:
        print(f"WARNING: Couldn't locate Schwab transactions file({transactions_file})")
        return []
