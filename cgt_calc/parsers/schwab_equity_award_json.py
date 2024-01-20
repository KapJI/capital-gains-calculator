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

from dataclasses import InitVar, dataclass
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

OPTIONAL_DETAILS_NAME = "Details"

field2schema = {"transactions": 1, "Transactions": 2}


@dataclass
class FieldNames:
    """Names of the fields in the Schwab JSON data, depending on the schema version."""

    # Note that the schema version is not an official Schwab one, just something
    # we use internally in this code:
    schema_version: InitVar[int] = 2

    transactions: str = "Transactions"
    description: str = "Description"
    action: str = "Action"
    symbol: str = "Symbol"
    quantity: str = "Quantity"
    amount: str = "Amount"
    fees: str = "FeesAndCommissions"
    transac_details: str = "TransactionDetails"
    vest_date: str = "VestDate"
    vest_fair_market_value: str = "VestFairMarketValue"
    award_date: str = "AwardDate"
    award_id: str = "AwardId"
    date: str = "Date"
    sale_price: str = "SalePrice"

    def __post_init__(self, schema_version: int) -> None:
        """Set correct field names if the schema is not the default one.

        Automatically run on object initialization.
        """
        print(f"{schema_version=}")
        if schema_version == 1:
            self.transactions = "transactions"
            self.description = "description"
            self.action = "action"
            self.symbol = "symbol"
            self.quantity = "quantitySortValue"
            self.amount = "amount"
            self.fees = "totalCommissionsAndFeesSortValue"
            self.transac_details = "transactionDetails"
            self.vest_date = "vestDate"
            self.vest_fair_market_value = "vestFairMarketValue"
            self.award_date = "awardDate"
            self.award_id = "awardName"
            self.date = "eventDate"
            self.sale_price = "salePrice"


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


def _round_decimal(num: Decimal) -> Decimal:
    # We want enough decimals to cover what Schwab gives us (2 decimals)
    # divided by the share-split factor (20), so we keep 4 decimals.
    # We don't want more decimals than necessary or we risk converting
    # the float number format approximations into Decimals
    # (e.g. a number 1.0001 in JSON may become 1.00010001 when parsed
    # into float, but we want to get Decimal('1.0001'))
    return num.quantize(Decimal(".0001")).normalize()


def _get_decimal_or_default(
    row: JsonRowType, key: str, default: Optional[Decimal] = None
) -> Optional[Decimal]:
    if key in row and row[key]:
        if isinstance(row[key], float):
            return _round_decimal(Decimal.from_float(row[key]))
        if row[key] is not None:
            return Decimal(row[key])

    return default


def _get_decimal(row: JsonRowType, key: str) -> Decimal:
    return _get_decimal_or_default(row, key, Decimal(0))  # type: ignore


def _price_from_str(price_str: str | None) -> Decimal:
    # example: "$1,250.00",
    # remove $ sign, and coma thousand separators:
    if price_str is None:
        return Decimal(0)

    return Decimal(price_str.replace("$", "").replace(",", ""))


def _get_decimal_from_price(row: JsonRowType, key: str) -> Decimal:
    if key in row and isinstance(row[key], str):
        return _price_from_str(row[key])

    return _get_decimal_or_default(row, key, Decimal(0))  # type: ignore


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(self, row: JsonRowType, file: str, field_names: FieldNames) -> None:
        """Create a new SchwabTransaction from a JSON row."""
        names = field_names
        description = row[names.description]
        self.raw_action = row[names.action]
        action = action_from_str(self.raw_action)
        symbol = row.get(names.symbol)
        quantity = _get_decimal_or_default(row, names.quantity)
        amount = _get_decimal_from_price(row, names.amount)
        fees = _get_decimal_from_price(row, names.fees)
        if row[names.action] == "Deposit":
            if len(row[names.transac_details]) != 1:
                raise ParsingError(
                    file,
                    "Expected a single Transaction Details for a Deposit, but "
                    f"found {len(row[names.transac_details])}",
                )
            if OPTIONAL_DETAILS_NAME in row[names.transac_details][0]:
                details = row[names.transac_details][0]["Details"]
            else:
                details = row[names.transac_details][0]
            date = datetime.datetime.strptime(
                details[names.vest_date], "%m/%d/%Y"
            ).date()
            price = _price_from_str(details[names.vest_fair_market_value])
            description = (
                f"Vest from Award Date "
                f"{details[names.award_date]} "
                f"(ID {details[names.award_id]})"
            )
        elif row[names.action] == "Sale":
            # Schwab's data export shows the settlement date,
            # whereas HMRC wants the trade date:
            date = (
                datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()
                - SETTLEMENT_DELAY
            ).date()

            if OPTIONAL_DETAILS_NAME in row[names.transac_details][0]:
                details = row[names.transac_details][0]["Details"]
            else:
                details = row[names.transac_details][0]

            # Schwab's data export lacks decimals on Sales quantities,
            # so we infer it from the amount and salePrice.
            price_str = details[names.sale_price]
            price = _price_from_str(price_str)

            # Schwab only gives us overall transaction amount, and sale price
            # of the sub-transactions. We can only work-out the correct
            # quantity if all sub-transactions have the same price:
            for subtransac in row[names.transac_details][1:]:
                if OPTIONAL_DETAILS_NAME in subtransac:
                    subtransac_details = subtransac["Details"]
                else:
                    subtransac_details = subtransac

                if subtransac_details[names.sale_price] != price_str:
                    raise ParsingError(
                        file,
                        "Impossible to work out quantity of sale of date"
                        f"{date} and amount {amount} because different "
                        "sub-transaction have different sale prices",
                    )

            quantity = (amount + fees) / price
        else:
            raise ParsingError(
                file, f"Parsing for action {row[names.action]} is not implemented!"
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
            self.price = _round_decimal(self.price / split_factor)
            self.quantity = _round_decimal(self.quantity * split_factor)


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

            for field_name, schema_version in field2schema.items():
                if field_name in data:
                    fields = FieldNames(schema_version)
                    break
            if not fields:
                raise ParsingError(
                    transactions_file,
                    f"Expected top level field ({', '.join(field2schema.keys())}) "
                    "not found: the JSON data is not in the expected format",
                )

            if not isinstance(data[fields.transactions], list):
                raise ParsingError(
                    transactions_file,
                    f"'{fields.transactions}' is not a list: the JSON data is not "
                    "in the expected format",
                )

            transactions = [
                SchwabTransaction(transac, transactions_file, fields)
                for transac in data[fields.transactions]
                # Skip as not relevant for CGT
                if transac[fields.action] not in {"Journal", "Wire Transfer"}
            ]
            transactions.reverse()
            return list(transactions)
    except FileNotFoundError:
        print(f"WARNING: Couldn't locate Schwab transactions file({transactions_file})")
        return []
