"""Charles Schwab Equity Award JSON export parser.

To get the data from Schwab:
1. Open https://client.schwab.com/Apps/accounts/transactionhistory/#/
2. Make sure Equity Award Center is selected
3. Select date range ALL and click SEARCH
4. In chrome devtools, look for an API call to
   https://ausgateway.schwab.com/api/is.TransactionHistoryWeb/TransactionHistoryInterface/TransactionHistory/equity-award-center/transactions
5. Copy response JSON inside schwab_input.json and run schwab.py
"""
from __future__ import annotations

import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Optional

from pandas.tseries.holiday import USFederalHolidayCalendar  # type: ignore
from pandas.tseries.offsets import CustomBusinessDay  # type: ignore

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction

# Delay between a (sale) trade, and when it is settled.
SETTLEMENT_DELAY = 2 * CustomBusinessDay(calendar=USFederalHolidayCalendar())

JsonRowType = Any  # type: ignore


def action_from_str(label: str) -> ActionType:
    """Convert string label to ActionType."""
    if label in {"Buy"}:
        return ActionType.BUY

    if label in {"Sell", "Sale"}:
        return ActionType.SELL

    if label in {
        "MoneyLink Transfer",
        "Misc Cash Entry",
        "Service Fee",
        "Wire Funds",
        "Wire Transfer",
        "Funds Received",
        "Journal",
        "Cash In Lieu",
    }:
        return ActionType.TRANSFER

    if label in {"Stock Plan Activity", "Deposit"}:
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

    if label == "Reinvest Shares":
        return ActionType.REINVEST_SHARES

    if label == "Reinvest Dividend":
        return ActionType.REINVEST_DIVIDENDS

    if label == "Wire Funds Received":
        return ActionType.WIRE_FUNDS_RECEIVED

    raise ParsingError("schwab transactions", f"Unknown action: {label}")


def _get_decimal_or_default(
    row: JsonRowType, key: str, default: Optional[Decimal] = None
) -> Optional[Decimal]:
    if key in row and row[key]:
        if isinstance(row[key], float):
            return round(Decimal.from_float(row[key]), 2)

        return Decimal(row[key])

    return default


def _get_decimal(row: JsonRowType, key: str) -> Decimal:
    return _get_decimal_or_default(row, key, Decimal(0))  # type: ignore


def _price_from_str(price_str: str) -> Decimal:
    # example: "$1,250.00",
    # remove $ sign, and coma thousand separators:
    return Decimal(price_str.replace("$", "").replace(",", ""))


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(
        self,
        row: JsonRowType,
        file: str,
    ) -> None:
        """Create a new SchwabTransaction from a JSON row."""
        description = row["description"]
        self.raw_action = row["action"]
        action = action_from_str(self.raw_action)
        symbol = row.get("symbol")
        quantity = _get_decimal_or_default(row, "quantitySortValue")
        amount = _get_decimal(row, "amountSortValue")
        fees = _get_decimal(row, "totalCommissionsAndFeesSortValue")
        if row["action"] == "Deposit":
            if len(row["transactionDetails"]) != 1:
                raise ParsingError(
                    file,
                    "Expected a single transactionDetails for a Deposit, but "
                    f"found {len(row['transactionDetails'])}",
                )
            date = datetime.datetime.strptime(
                row["transactionDetails"][0]["vestDate"], "%m/%d/%Y"
            ).date()
            price = _price_from_str(row["transactionDetails"][0]["vestFairMarketValue"])
            description = (
                f"Vest from Award Date "
                f'{row["transactionDetails"][0]["awardDate"]} '
                f'(ID {row["transactionDetails"][0]["awardName"]})'
            )
        elif row["action"] == "Sale":
            # Schwab's data export shows the settlement date,
            # whereas HMRC wants the trade date:
            date = (
                datetime.datetime.strptime(row["eventDate"], "%m/%d/%Y").date()
                - SETTLEMENT_DELAY
            ).date()
            # Schwab's data export lacks decimals on Sales quantities,
            # so we infer it from the amount and salePrice.
            price_str = row["transactionDetails"][0]["salePrice"]
            price = _price_from_str(price_str)

            # Schwab only gives us overall transaction amount, and sale price
            # of the sub-transactions. We can only work-out the correct
            # quantity if all sub-transactions have the same price:
            for subtransac in row["transactionDetails"][1:]:
                if subtransac["salePrice"] != price_str:
                    raise ParsingError(
                        file,
                        "Impossible to work out quantity of sale of date"
                        f"{date} and amount {amount} because different "
                        "sub-transaction have different sale prices",
                    )

            quantity = (amount + fees) / price
        else:
            raise ParsingError(
                file, f'Parsing for action {row["action"]} is not implemented!'
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

        self._normalize_split()

    def _normalize_split(self) -> None:
        """Ensure past transactions are normalized to split values.

        This is in the context of the 20:1 stock split which happened at close
        on 2022-07-15 20:1.

        As of 2022-08-07, Schwab's data exports have some past transactions
        corrected for the 20:1 split on 2022-07-15, whereas others are not.
        """
        split_factor = 20

        # The share price has never been above $175*20=$3500 before 2022-07-15
        # so this price is expressed in pre-split amounts: normalize to post-split
        if (
            self.date <= datetime.date(2022, 7, 15)
            and self.price
            and self.price > 175
            and self.quantity
        ):
            self.price = round(self.price / split_factor, 2)
            self.quantity = round(self.quantity * split_factor, 2)


def read_schwab_equity_award_json_transactions(
    transactions_file: str,
) -> list[BrokerTransaction]:
    """Read Schwab transactions from file."""
    try:
        with Path(transactions_file).open(encoding="utf-8") as json_file:
            try:
                data = json.load(json_file)
            except json.decoder.JSONDecodeError as exception:
                raise ParsingError(
                    transactions_file,
                    "Cloud not parse content as JSON",
                ) from exception

            if "transactions" not in data or not isinstance(data["transactions"], list):
                raise ParsingError(
                    transactions_file,
                    "no 'transactions' list found: the JSON data is not "
                    "in the expected format",
                )

            transactions = [
                SchwabTransaction(transac, transactions_file)
                for transac in data["transactions"]
                # Skip as not relevant for CGT
                if transac["action"] not in {"Journal", "Wire Transfer"}
            ]
            transactions.reverse()
            return list(transactions)
    except FileNotFoundError:
        print(f"WARNING: Couldn't locate Schwab transactions file({transactions_file})")
        return []
