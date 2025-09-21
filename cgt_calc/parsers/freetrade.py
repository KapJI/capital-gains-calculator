"""Freetrade parser."""

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
    "Title",
    "Type",
    "Timestamp",
    "Account Currency",
    "Total Amount",
    "Buy / Sell",
    "Ticker",
    "ISIN",
    "Price per Share in Account Currency",
    "Stamp Duty",
    "Quantity",
    "Venue",
    "Order ID",
    "Order Type",
    "Instrument Currency",
    "Total Shares Amount",
    "Price per Share",
    "FX Rate",
    "Base FX Rate",
    "FX Fee (BPS)",
    "FX Fee Amount",
    "Dividend Ex Date",
    "Dividend Pay Date",
    "Dividend Eligible Quantity",
    "Dividend Amount Per Share",
    "Dividend Gross Distribution Amount",
    "Dividend Net Distribution Amount",
    "Dividend Withheld Tax Percentage",
    "Dividend Withheld Tax Amount",
]


def decimal_or_none(val: str) -> Decimal | None:
    """Convert value to Decimal."""
    return Decimal(val) if val not in ["", "Not available"] else None


def is_relevant_transaction(type_label: str) -> bool:
    """Check if transaction type is relevant for CGT calculations."""
    return type_label not in [
        "MONTHLY_STATEMENT",
        "MONTHLY_SHARE_LENDING_STATEMENT",
        "TAX_CERTIFICATE",
    ]


def action_from_str(type_label: str, buy_sell: str, filename: str) -> ActionType:
    """Convert type and buy/sell to ActionType."""
    if type_label == "ORDER":
        if buy_sell == "BUY":
            return ActionType.BUY
        if buy_sell == "SELL":
            return ActionType.SELL
        raise ParsingError(filename, f"Unknown ORDER action: {buy_sell}")

    if type_label == "FREESHARE_ORDER":
        # Freeshare orders are free shares given by Freetrade (like stock grants)
        return ActionType.STOCK_ACTIVITY

    if type_label == "DIVIDEND":
        return ActionType.DIVIDEND

    if type_label == "SPECIAL_DIVIDEND":
        return ActionType.DIVIDEND

    if type_label in ["TOP_UP", "WITHDRAWAL"]:
        return ActionType.TRANSFER

    if type_label == "INTEREST_FROM_CASH":
        return ActionType.INTEREST

    raise ParsingError(filename, f"Unknown transaction type: {type_label}")


class FreetradeDividend:
    """Helper class for dividend data."""

    def __init__(self, row: dict[str, str]):
        """Initialize dividend data from row."""
        self.ex_date_str = row.get("Dividend Ex Date", "")
        self.pay_date_str = row.get("Dividend Pay Date", "")
        self.eligible_quantity = decimal_or_none(row.get("Dividend Eligible Quantity"))
        self.amount_per_share = decimal_or_none(row.get("Dividend Amount Per Share"))
        self.gross_amount = decimal_or_none(row.get("Dividend Gross Distribution Amount"))
        self.net_amount = decimal_or_none(row.get("Dividend Net Distribution Amount"))
        self.withholding_tax_percentage = decimal_or_none(
            row.get("Dividend Withheld Tax Percentage")
        )
        self.withholding_tax_amount = decimal_or_none(
            row.get("Dividend Withheld Tax Amount")
        )


class FreetradeTransaction(BrokerTransaction):
    """Represent single Freetrade transaction."""

    def __init__(self, header: list[str], row_raw: list[str], filename: str, isin_to_ticker: dict[str, str] | None = None):
        """Create transaction from CSV row."""
        row = dict(zip(header, row_raw))
        if isin_to_ticker is None:
            isin_to_ticker = {}

        # Parse timestamp (ISO format)
        timestamp_str = row["Timestamp"]
        if timestamp_str:
            # Remove Z and handle timezone
            timestamp_str = timestamp_str.replace("Z", "+00:00")
            self.datetime = datetime.fromisoformat(timestamp_str)
            date = self.datetime.date()
        else:
            raise ParsingError(filename, "Missing timestamp")

        self.raw_type = row["Type"]
        self.raw_buy_sell = row.get("Buy / Sell", "")

        # Determine action type
        action = action_from_str(self.raw_type, self.raw_buy_sell, filename)

        # Parse basic fields
        symbol = row.get("Ticker") if row.get("Ticker") else None
        isin = row.get("ISIN", "")

        # If ticker is empty but ISIN is available, try to map from ISIN to ticker
        if not symbol and isin and isin in isin_to_ticker:
            symbol = isin_to_ticker[isin]

        if symbol:
            symbol = TICKER_RENAMES.get(symbol, symbol)

        description = row.get("Title", "")

        # Parse amounts and quantities
        total_amount = decimal_or_none(row.get("Total Amount"))
        quantity = decimal_or_none(row.get("Quantity"))
        price_gbp = decimal_or_none(row.get("Price per Share in Account Currency"))

        # Parse foreign currency data
        self.instrument_currency = row.get("Instrument Currency", "GBP")
        self.price_foreign = decimal_or_none(row.get("Price per Share"))
        self.total_shares_amount = decimal_or_none(row.get("Total Shares Amount"))
        self.fx_rate = decimal_or_none(row.get("FX Rate"))

        # Parse fees
        stamp_duty = decimal_or_none(row.get("Stamp Duty")) or Decimal("0")
        fx_fee = decimal_or_none(row.get("FX Fee Amount")) or Decimal("0")
        fees = stamp_duty + fx_fee

        # For FREESHARE_ORDER, don't include fees in the cost basis since they're promotional shares
        if self.raw_type == "FREESHARE_ORDER":
            fees = Decimal("0")

        # Handle dividend specific data
        if action == ActionType.DIVIDEND:
            self.dividend = FreetradeDividend(row)
            # For dividends, quantity might be in dividend fields
            if quantity is None and self.dividend.eligible_quantity:
                quantity = self.dividend.eligible_quantity
            # Use net dividend amount if available
            if total_amount is None and self.dividend.net_amount:
                total_amount = self.dividend.net_amount

        # Handle price calculation
        # For buys, always recalculate price since Total Amount includes fees but we need price excluding fees
        if action == ActionType.BUY and quantity and total_amount and quantity > 0:
            price = (total_amount - fees) / quantity
        # For STOCK_ACTIVITY (like FREESHARE_ORDER), use total amount as price since no fees apply
        elif action == ActionType.STOCK_ACTIVITY and quantity and total_amount and quantity > 0:
            price = total_amount / quantity
        elif price_gbp is not None:
            price = price_gbp
        elif quantity and total_amount and quantity > 0:
            price = total_amount / quantity
        else:
            price = None

        # Determine amount (negative for buys and withdrawals, positive for sells and deposits)
        amount = total_amount

        # STOCK_ACTIVITY (like FREESHARE_ORDER) has no cash flow impact
        if action == ActionType.STOCK_ACTIVITY:
            amount = None
        elif action == ActionType.BUY or (action == ActionType.TRANSFER and self.raw_type == "WITHDRAWAL"):
            if amount:
                amount = -amount

        # For transfers without symbols, use the transaction type as description
        if action == ActionType.TRANSFER and not symbol:
            description = self.raw_type.replace("_", " ").title()

        broker = "Freetrade"
        super().__init__(
            date=date,
            action=action,
            symbol=symbol,
            description=description,
            quantity=quantity,
            price=price,
            fees=fees,
            amount=amount,
            currency="GBP",
            broker=broker,
        )


def read_freetrade_transactions(transactions_folder: str) -> list[BrokerTransaction]:
    """Read Freetrade transactions from CSV files in folder."""
    transactions = []
    isin_to_ticker: dict[str, str] = {}

    # First pass: collect ISIN to ticker mappings
    for file in Path(transactions_folder).glob("*.csv"):
        with Path(file).open(encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader)

            for row_raw in reader:
                if not any(row_raw):  # Skip empty rows
                    continue

                row_dict = dict(zip(header, row_raw))
                transaction_type = row_dict.get("Type", "")

                if not is_relevant_transaction(transaction_type):
                    continue  # Skip irrelevant transactions

                ticker = row_dict.get("Ticker", "").strip()
                isin = row_dict.get("ISIN", "").strip()

                # Build ISIN to ticker mapping from transactions that have both
                if ticker and isin:
                    isin_to_ticker[isin] = ticker

    # Second pass: process transactions with ISIN mapping available
    for file in Path(transactions_folder).glob("*.csv"):
        with Path(file).open(encoding="utf-8") as csvfile:
            print(f"Parsing {file}")
            reader = csv.reader(csvfile)
            header = next(reader)

            for row_raw in reader:
                if not any(row_raw):  # Skip empty rows
                    continue

                # Check if this transaction type is relevant before creating object
                row_dict = dict(zip(header, row_raw))
                transaction_type = row_dict.get("Type", "")

                if not is_relevant_transaction(transaction_type):
                    continue  # Skip irrelevant transactions

                try:
                    transaction = FreetradeTransaction(header, row_raw, str(file), isin_to_ticker)
                    transactions.append(transaction)
                except ParsingError:
                    # Re-raise parsing errors
                    raise
                except Exception as e:
                    raise ParsingError(str(file), f"Failed to parse row: {e}") from e

    # Sort transactions by datetime to preserve intraday order
    transactions.sort(key=lambda t: t.datetime)
    return transactions