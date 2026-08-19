"""Charles Schwab Equity Award JSON export parser.

To get the data from Schwab:
1. Open https://client.schwab.com/app/accounts/history/#/
2. Make sure Equity Award Center is selected
3. Select date range 'Previous 4 Years' and click SEARCH
4. At the top right, click on Export and select type JSON
5. If you have had Equity Awards for more than 4 years, good news:
   Schwab now allows to export all the data history (which you do need
   to calculate the CGT). In that case:
   * repeat the process to export older data
   * manually combine the data into a single file
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
import datetime
from decimal import Decimal
from enum import Enum, auto
import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Final, TextIO, override

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.util import round_decimal

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

OPTIONAL_DETAILS_NAME: Final = "Details"
FIELD_TO_SCHEMA: Final = {"transactions": 1, "Transactions": 2}
LOGGER = logging.getLogger(__name__)


class Restatement(Enum):
    """What the export did to records that predate a split."""

    ACQUISITIONS_ONLY = auto()
    """Acquisitions restated into current units, disposals left in their own.

    Only disposals need converting, and the split dates alone say by how much.
    """

    UNRELIABLE = auto()
    """Some records restated and some not, with nothing in the file saying which.

    The price has to give the units away, so this needs `presplit_price_floor`.
    """


@dataclass(frozen=True)
class SplitHistory:
    """Splits for one symbol, and what the export did to records before them."""

    splits: tuple[tuple[datetime.date, int], ...]
    """Ratios against the first day on which counts are in the new units."""

    restatement: Restatement

    presplit_price_floor: Decimal | None = None
    """A price above which a row is certainly still in pre-split units.

    Set it above the highest price the share reached before its earliest split
    and below the lowest price it can plausibly reach after: everything in
    between is indistinguishable. Only meaningful with UNRELIABLE.
    """

    def __post_init__(self) -> None:
        """Reject a table that cannot be applied."""
        if (self.restatement is Restatement.UNRELIABLE) != (
            self.presplit_price_floor is not None
        ):
            raise ValueError(
                "presplit_price_floor is required for UNRELIABLE and "
                "meaningless otherwise"
            )


# Splits dated to the first day on which the export states counts in the new
# units, which is the day after a split effective at the close.
#
# Dates rather than a price heuristic wherever the export allows it: a
# heuristic needs the market price of the day, which means a network call, and
# it stops working as soon as the real price passes the threshold it guesses
# with. GOOG is UNRELIABLE only because its export leaves nothing better.
SPLITS: Final = {
    "NVDA": SplitHistory(
        ((datetime.date(2021, 7, 20), 4), (datetime.date(2024, 6, 10), 10)),
        Restatement.ACQUISITIONS_ONLY,
    ),
    "GOOG": SplitHistory(
        ((datetime.date(2022, 7, 16), 20),),
        Restatement.UNRELIABLE,
        # GOOG never traded above $175 * 20 = $3500 before the split.
        presplit_price_floor=Decimal(175),
    ),
    "GOOGL": SplitHistory(
        ((datetime.date(2022, 7, 16), 20),),
        Restatement.UNRELIABLE,
        presplit_price_floor=Decimal(175),
    ),
}


def split_multiplier(symbol: str | None, on: datetime.date) -> int:
    """Factor turning a share count printed on `on` into current units.

    Prices divide by the same factor, so the money is unchanged.
    """
    history = SPLITS.get(symbol or "")
    if history is None:
        return 1
    factor = 1
    for effective, ratio in history.splits:
        if on < effective:
            factor *= ratio
    return factor


def _restates_acquisitions_only(symbol: str | None) -> bool:
    """Whether disposals of `symbol` are the only records left un-restated."""
    history = SPLITS.get(symbol or "")
    return history is not None and history.restatement is Restatement.ACQUISITIONS_ONLY


def _detail_counts_multiplier(symbol: str | None, on: datetime.date) -> int:
    """Factor turning counts inside a row's details into current units.

    A row's own `Quantity` is restated for later splits while the counts inside
    its `TransactionDetails` are in the units of their own day. The two are
    therefore only comparable through this factor, and a count taken from the
    details only reaches the pool through it.

    Returns 1 where the export does not restate uniformly, because there the
    difference cannot be told apart in the first place.
    """
    if not _restates_acquisitions_only(symbol):
        return 1
    return split_multiplier(symbol, on)


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
    shares: str = "Shares"
    vest_date: str = "VestDate"
    vest_fair_market_value: str = "VestFairMarketValue"
    award_date: str = "AwardDate"
    award_id: str = "AwardId"
    date: str = "Date"
    sale_price: str = "SalePrice"
    purchase_date: str = "PurchaseDate"
    purchase_fair_market_value: str = "PurchaseFairMarketValue"
    net_shares_deposited: str = "NetSharesDeposited"
    shares_withheld: str = "SharesWithheld"
    shares_sold: str = "SharesSold"
    shares_sold_withheld_for_taxes: str = "SharesSoldWithheldForTaxes"
    tax_withholding_method: str = "TaxWithholdingMethod"

    def __post_init__(self, schema_version: int) -> None:
        """Set correct field names if the schema is not the default one.

        Automatically run on object initialization.
        """
        if schema_version == 1:
            self.transactions = "transactions"
            self.description = "description"
            self.action = "action"
            self.symbol = "symbol"
            self.quantity = "quantity"
            self.amount = "amount"
            self.fees = "totalCommissionsAndFees"
            self.transac_details = "transactionDetails"
            self.shares = "shares"
            self.vest_date = "vestDate"
            self.vest_fair_market_value = "vestFairMarketValue"
            self.award_date = "awardDate"
            self.award_id = "awardName"
            self.date = "eventDate"
            self.sale_price = "salePrice"
            self.purchase_date = "purchaseDate"
            self.purchase_fair_market_value = "purchaseFairMarketValue"
            self.net_shares_deposited = "netSharesDeposited"
            self.shares_withheld = "sharesWithheld"
            self.shares_sold = "sharesSold"
            self.shares_sold_withheld_for_taxes = "sharesSoldWithheldForTaxes"
            self.tax_withholding_method = "taxWithholdingMethod"


# We want enough decimals to cover what Schwab gives us (up to 4 decimals)
# divided by the share-split factor (20), so we keep 6 decimals.
# We don't want more decimals than necessary or we risk converting
# the float number format approximations into Decimals
# (e.g. a number 1.0001 in JSON may become 1.00010001 when parsed
# into float, but we want to get Decimal('1.0001'))
ROUND_DIGITS = 6

JsonRowType = Any  # type: ignore[explicit-any]


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""
    if label == "Buy":
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

    if label in ["Qualified Dividend", "Cash Dividend", "Dividend"]:
        return ActionType.DIVIDEND

    if label in [
        "NRA Tax Adj",
        "NRA Withholding",
        "Foreign Tax Paid",
        "Tax Reversal",
        "Tax Withholding",
    ]:
        return ActionType.DIVIDEND_TAX

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

    raise ParsingError(file, f"Unknown action: {label}")


def _decimal_from_str(price_str: str) -> Decimal:
    """Convert a number as string to a Decimal.

    Remove $ sign, and comma thousand separators so as to handle dollar amounts
    such as "$1,250.00".
    """
    return Decimal(price_str.replace("$", "").replace(",", ""))


def _decimal_from_number_or_str(
    row: JsonRowType,
    field_basename: str,
    field_float_suffix: str = "SortValue",
) -> Decimal:
    """Get a number from a row, preferably from the number field.

    Fall back to the string representation field, or default to Decimal(0)
    if the fields are not there or both have a value of None or empty string.
    """
    # We prefer native number to strings as more efficient/safer parsing
    float_name = f"{field_basename}{field_float_suffix}"
    if float_name in row and row[float_name] is not None:
        return Decimal(row[float_name])

    # An empty string means the field is not filled in, the same as it being
    # absent. Schwab writes one for FeesAndCommissions on a commission-free
    # sale. Note this is deliberately not done inside _decimal_from_str: an
    # empty *price* must raise rather than become a zero cost basis, which
    # would be taxed in full.
    if (
        field_basename in row
        and row[field_basename] is not None
        and row[field_basename] != ""
    ):
        return _decimal_from_str(row[field_basename])

    return Decimal(0)


def _is_integer(number: Decimal) -> bool:
    return number % 1 == 0


def _optional_decimal(details: JsonRowType, field_name: str) -> Decimal | None:
    """Read a count Schwab may leave blank.

    An absent field and an empty one mean the same thing: Schwab had nothing to
    put there. A "0" does not — it is a count, and a real one.
    """
    value = details.get(field_name)
    if value is None or value == "":
        return None
    return _decimal_from_str(value)


def _lot_share_sum(row: JsonRowType, names: FieldNames) -> Decimal | None:
    """Recover a sale's quantity where the row rounds it and the lots do not.

    Returns None when the lots cannot answer: one of them states no count, or
    they are all whole and so cannot hold a fraction the row dropped.
    """
    total = Decimal(0)
    found_decimals = False
    for subtransac in row[names.transac_details]:
        details = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)
        if names.shares not in details:
            return None
        shares = _decimal_from_str(details[names.shares])
        total += shares
        if not _is_integer(shares):
            found_decimals = True
    return total if found_decimals else None


def _lot_price(row: JsonRowType, names: FieldNames) -> Decimal | None:
    """Return the one price every lot sold at, or None if they differ."""
    prices = set()
    for subtransac in row[names.transac_details]:
        details = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)
        if names.sale_price not in details:
            return None
        prices.add(details[names.sale_price])
    if len(prices) != 1:
        return None
    return _decimal_from_str(prices.pop())


def _recovered_sale_quantity(
    row: JsonRowType,
    names: FieldNames,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
) -> Decimal | None:
    """Return the count a sale really disposed of, where its Quantity rounds it.

    The lots answer first, when they kept the decimals the row dropped. Failing
    that the money answers, provided every lot sold at one price — and that is
    the only witness left when a lot stating zero shares hides the fraction
    from both count fields at once.

    The money is only consulted when the row does not already reconcile with
    it, because the price is rounded for display and recomputing from it turns
    an exact 5 into 4.999995.

    Returns None when neither answers: lots sold at different prices leave the
    row's own figure as the best there is.
    """
    lots = _lot_share_sum(row, names)
    if lots is not None:
        return lots

    price = _lot_price(row, names)
    if not price:
        return None
    if round_decimal(quantity * price - fees, 2) == amount:
        return None
    return (amount + fees) / price


def _espp_net_shares(
    details: JsonRowType,
    names: FieldNames,
    quantity: Decimal,
    multiplier: int,
    file: Path,
) -> Decimal:
    """How many of an ESPP purchase's shares reach the pool.

    All of them, unless tax was settled out of the purchase itself, in which
    case what was deposited, withheld and sold has to add back up to what was
    bought. Shares taken for tax were never acquired, so they are not a
    disposal and produce no gain; they simply never enter the pool.

    The counts are in the units of the purchase date and `quantity` is
    restated, so both the identity and the pooled figure go through
    `multiplier`. Without it a purchase made before a later split reads as one
    that does not add up, and the file stops parsing.
    """
    deposited = _optional_decimal(details, names.net_shares_deposited)
    withheld = _optional_decimal(details, names.shares_withheld)
    sold = _optional_decimal(details, names.shares_sold)
    method = details.get(names.tax_withholding_method)

    if deposited is None and withheld is None and sold is None and not method:
        return quantity

    when = details.get(names.purchase_date, "an unknown date")

    if deposited is None:
        raise ParsingError(
            file,
            f"ESPP purchase on {when} settles tax in shares ({method}) but "
            "does not say how many were deposited",
        )

    accounted = (
        deposited + (withheld or Decimal(0)) + (sold or Decimal(0))
    ) * multiplier
    if accounted != quantity:
        raise ParsingError(
            file,
            f"ESPP purchase on {when} bought {quantity} shares but accounts "
            f"for {accounted}: {deposited} deposited, {withheld or 0} "
            f"withheld, {sold or 0} sold, at a split multiplier of "
            f"{multiplier}. Pooling the deposited count alone would drop the "
            "difference without saying so",
        )

    return deposited * multiplier


def _check_lapse_units(
    rows: list[JsonRowType], file_path: Path, names: FieldNames
) -> None:
    """Check the split table against the record that states both units.

    A Lapse is not an acquisition and is discarded, but on the way past it
    proves something no other record can. Its Quantity is restated for later
    splits while the counts inside it are in the units of their own day, so the
    two are related by exactly the split multiplier and nothing else.

    That makes the split table falsifiable using only the file. If a date is
    wrong, or a split has happened that this code has not been told about, it
    surfaces here — before any disposal has been converted on the strength of
    it.
    """
    for row in rows:
        if row.get(names.action) != "Lapse":
            continue
        symbol = row.get(names.symbol)
        symbol = TICKER_RENAMES.get(symbol, symbol)
        if not _restates_acquisitions_only(symbol):
            continue

        detail_rows = row.get(names.transac_details) or []
        if not detail_rows:
            continue
        details = detail_rows[0].get(OPTIONAL_DETAILS_NAME, detail_rows[0])

        deposited = _optional_decimal(details, names.net_shares_deposited)
        withheld = _optional_decimal(details, names.shares_sold_withheld_for_taxes)
        if deposited is None or withheld is None:
            continue

        date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()
        multiplier = _detail_counts_multiplier(symbol, date)
        quantity = _decimal_from_number_or_str(row, names.quantity)
        expected = (deposited + withheld) * multiplier

        if quantity != expected:
            raise ParsingError(
                file_path,
                f"The lapse on {row[names.date]} reports {quantity} shares, "
                f"but {deposited} deposited plus {withheld} withheld at a "
                f"split multiplier of {multiplier} makes {expected}. Either "
                "the split table in this file is wrong or a split is missing "
                "from it; every disposal before that split would be converted "
                "by the wrong factor",
            )


def _check_disposal_units(
    transactions: Sequence[BrokerTransaction], file_path: Path
) -> None:
    """Warn where a disposal looks like it had already been restated.

    Converting disposals rests on Schwab restating acquisitions and leaving
    disposals alone. That is what its NVDA exports do, but it is an observation
    about the file rather than a guarantee, and the GOOG handling in this same
    parser exists because Schwab restated some records and not others.

    Acquisitions are the yardstick: once a disposal is converted the two are in
    the same units, so a disposal should cost about what shares cost around it,
    and one converted twice is cheaper by the whole multiplier.

    This warns rather than raises because the yardstick is a price. A share
    that halves between the nearest vest and the sale trips the 4:1 threshold
    on its own, which is a week NVDA has had more than once. The checks that do
    raise are the ones a price move cannot reach.
    """
    acquisitions = [
        transaction
        for transaction in transactions
        if transaction.action == ActionType.STOCK_ACTIVITY
        and transaction.price
        and _restates_acquisitions_only(transaction.symbol)
    ]
    if not acquisitions:
        return

    for transaction in transactions:
        if (
            transaction.action != ActionType.SELL
            or not _restates_acquisitions_only(transaction.symbol)
            or not transaction.price
        ):
            continue

        multiplier = split_multiplier(transaction.symbol, transaction.date)
        if multiplier == 1:
            continue

        same_symbol = [
            acquisition
            for acquisition in acquisitions
            if acquisition.symbol == transaction.symbol
        ]
        if not same_symbol:
            continue

        nearest = min(
            same_symbol,
            key=lambda acquisition: abs((acquisition.date - transaction.date).days),
        )
        if not nearest.price:
            continue

        ratio = transaction.price / nearest.price

        # Between "these agree" and "this one is a multiplier too cheap", take
        # whichever is nearer on a log scale: ratio < 1/sqrt(multiplier), or
        # ratio^2 * multiplier < 1 without a square root. The two hypotheses
        # are a factor of 4 apart at the very least, so the midpoint is not a
        # threshold that needs tuning.
        if ratio * ratio * multiplier < 1:
            LOGGER.warning(
                "%s: the disposal on %s works out at %s a share once converted "
                "for the %s:1 split, against %s for the acquisition on %s. "
                "Being cheaper by about the split multiplier is what it looks "
                "like when Schwab had already restated a disposal and it was "
                "converted a second time, which would take the wrong number of "
                "shares out of the pool while leaving the proceeds right. A "
                "share that simply fell that far between the two dates looks "
                "the same, so check it rather than trust it",
                file_path,
                transaction.date,
                round_decimal(transaction.price, 4),
                multiplier,
                round_decimal(nearest.price, 4),
                nearest.date,
            )


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(self, row: JsonRowType, file: Path, field_names: FieldNames) -> None:
        """Create a new SchwabTransaction from a JSON row."""
        names = field_names
        description = row[names.description]
        self.raw_action = row[names.action]
        action = action_from_str(self.raw_action, file)
        symbol = row.get(names.symbol)
        symbol = TICKER_RENAMES.get(symbol, symbol)
        if symbol not in SPLITS:
            LOGGER.warning(
                "The Schwab Equity Award JSON parser was only tested for the "
                "stocks whose splits it knows (%s), but found symbol '%s'. "
                "Everything else is parsed as usual; what is not applied is "
                "stock split normalization, which this parser only knows for "
                "the symbols above. If '%s' has split since the earliest "
                "transaction here, check the share counts against a broker "
                "statement before filing.",
                ", ".join(sorted(SPLITS)),
                symbol,
                symbol,
            )
        quantity = _decimal_from_number_or_str(row, names.quantity)
        initially_parsed_amount = _decimal_from_number_or_str(row, names.amount)
        amount = initially_parsed_amount
        fees = _decimal_from_number_or_str(row, names.fees)
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

            if description == "ESPP":
                # An ESPP purchase, not a vest, and it carries different
                # fields. The cost basis is the market value on the purchase
                # date rather than the discounted price paid, because the
                # discount is taxed as employment income through payroll.
                # Using the price paid understates the basis roughly tenfold
                # on the older purchases.
                date = datetime.datetime.strptime(
                    details[names.purchase_date], "%m/%d/%Y"
                ).date()
                price = _decimal_from_str(details[names.purchase_fair_market_value])

                # Since August 2025 the tax on a purchase is settled out of the
                # shares themselves, and only the rest reaches the pool. Before
                # that these fields are empty and Quantity is already net.
                #
                # Whether the field holds anything is not the signal. An empty
                # string and a "0" both do, and they mean opposite things:
                # nothing was withheld, and everything was. Reading either as
                # "use this count" loses the whole purchase from the pool
                # without saying so.
                #
                # So the counts have to account for every share bought. That
                # settles it either way, and it is the same identity a Lapse
                # has to satisfy.
                quantity = _espp_net_shares(
                    details,
                    names,
                    quantity,
                    _detail_counts_multiplier(symbol, date),
                    file,
                )

                amount = price * quantity
                description = f"ESPP purchase on {details[names.purchase_date]}"
            else:
                date = datetime.datetime.strptime(
                    details[names.vest_date], "%m/%d/%Y"
                ).date()
                # Schwab only provide this one as string:
                price = _decimal_from_str(details[names.vest_fair_market_value])
                if amount == Decimal(0):
                    amount = price * quantity
                description = (
                    f"Vest from Award Date "
                    f"{details[names.award_date]} "
                    f"(ID {details[names.award_id]})"
                )
        elif row[names.action] == "Sale":
            date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()

            if _restates_acquisitions_only(symbol):
                # Derive the price from the money rather than from the per-lot
                # SalePrice. Amount is net of fees, so the gross is rebuilt
                # first. Two reasons this is not just a shortcut:
                #
                # A disposal can span lots sold at different prices — the
                # 600-share sale of 30 May 2025 has two — and the branch below
                # refuses those outright. And the per-lot price is printed in
                # the units of the trade date while the pool is in current
                # ones, so it would need converting anyway. Working from the
                # money sidesteps both, and the money is the same in any units.
                #
                # The count still has to be recovered when the row has rounded
                # it away, or the pool loses the fraction silently.
                quantity = (
                    _recovered_sale_quantity(row, names, quantity, amount, fees)
                    or quantity
                )
                price = (amount + fees) / quantity
            elif not _is_integer(quantity):
                price = (amount + fees) / quantity
            else:
                subtransac_have_quantities = True
                subtransac_shares_sum = Decimal()  # Decimal 0
                found_share_decimals = False

                details = row[names.transac_details][0].get(
                    OPTIONAL_DETAILS_NAME, row[names.transac_details][0]
                )

                for subtransac in row[names.transac_details]:
                    subtransac = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)

                    if "shares" in subtransac:
                        # Schwab only provides this one as a string:
                        shares = _decimal_from_str(subtransac[names.shares])
                        subtransac_shares_sum += shares
                        if not _is_integer(shares):
                            found_share_decimals = True
                    else:
                        subtransac_have_quantities = False
                        break

                if subtransac_have_quantities and found_share_decimals:
                    quantity = subtransac_shares_sum
                    price = (amount + fees) / quantity
                else:
                    # Schwab sometimes only gives us overall transaction
                    # amount, and sale price of the sub-transactions.
                    # We can only work-out the correct quantity if all
                    # sub-transactions have the same price:

                    first_subtransac = row[names.transac_details][0]
                    first_subtransac = first_subtransac.get(
                        OPTIONAL_DETAILS_NAME, first_subtransac
                    )
                    price_str = first_subtransac[names.sale_price]
                    price = _decimal_from_str(price_str)

                    for subtransac in row[names.transac_details][1:]:
                        subtransac = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)

                        if subtransac[names.sale_price] != price_str:
                            raise ParsingError(
                                file,
                                "Impossible to work out quantity of sale of "
                                f"date {date} and amount {amount} because "
                                "different sub-transaction have different sale"
                                " prices",
                            )

                    # Check if the transaction was lacking decimals. Sometimes
                    # we did just sell an integer number of shares.
                    # If we unconditionally perform the quantity update step
                    # here, small roundings that were necessary to determine
                    # the initially_parsed_amount on the broker side can cause
                    # tiny decimal deviations in the quantity, which can then
                    # cause errors later on, e.g. triggering assertions about
                    # selling more than was available.
                    if (
                        round_decimal(quantity * price - fees, 2)
                        != initially_parsed_amount
                    ):
                        quantity = (amount + fees) / price

        elif action in [ActionType.DIVIDEND, ActionType.DIVIDEND_TAX]:
            date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()
            price = None
            amount = _decimal_from_str(row[names.amount])
            description = self.raw_action
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

        history = SPLITS.get(symbol or "")
        if history is None:
            return
        if history.restatement is Restatement.ACQUISITIONS_ONLY:
            self._convert_disposal_to_current_units()
        else:
            self._convert_row_priced_before_a_split(history)

    def _convert_disposal_to_current_units(self) -> None:
        """Convert a disposal into current share units.

        Schwab restates acquisitions for later splits — an NVDA vest of 3,000
        at $22.341 is recorded that way even though 300 at $223.41 is what
        happened — but leaves disposals in the units of their own day. Only
        disposals need converting.

        Missing this costs 54 shares on the two 2023 disposals, and the pool
        stays wrong by that much for every year afterwards.
        """
        if self.action != ActionType.SELL:
            return

        self._rescale(split_multiplier(self.symbol, self.date))

    def _convert_row_priced_before_a_split(self, history: SplitHistory) -> None:
        """Convert a row whose price shows it was never restated.

        Where the export restates some records and not others, with nothing
        saying which, the price is the only witness left: above the floor it
        can only be pre-split money. GOOG is the reason this exists — as of
        2022-08-07 its exports were restated only in part.
        """
        floor = history.presplit_price_floor
        assert floor is not None  # guaranteed by SplitHistory.__post_init__
        if self.price and self.price > floor and self.quantity:
            self._rescale(split_multiplier(self.symbol, self.date))

    def _rescale(self, multiplier: int) -> None:
        """Restate this row in current units, leaving the money unchanged."""
        if multiplier == 1:
            return
        if self.quantity:
            self.quantity = round_decimal(self.quantity * multiplier, ROUND_DIGITS)
        if self.price:
            self.price = round_decimal(self.price / multiplier, ROUND_DIGITS)


class SchwabEquityAwardsJSONParser(BaseSingleFileParser):
    """Parser for RAW format transaction files."""

    arg_name = "schwab-equity-award"
    pretty_name = "Charles Schwab Equity Awards"
    format_name = "JSON"
    deprecated_flags: ClassVar[list[str]] = ["--schwab_equity_award_json"]

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Schwab Equity Awards transactions from JSON."""
        try:
            data = json.load(file, parse_float=Decimal, parse_int=Decimal)
        except json.decoder.JSONDecodeError as exception:
            raise ParsingError(
                file_path,
                "Cloud not parse content as JSON",
            ) from exception

        fields: FieldNames | None = None
        for field_name, schema_version in FIELD_TO_SCHEMA.items():
            if field_name in data:
                fields = FieldNames(schema_version)
                break
        if fields is None:
            raise ParsingError(
                file_path,
                f"Expected top level field ({', '.join(FIELD_TO_SCHEMA.keys())}) "
                "not found: the JSON data is not in the expected format",
            )

        if not isinstance(data[fields.transactions], list):
            raise ParsingError(
                file_path,
                f"'{fields.transactions}' is not a list: the JSON data is not "
                "in the expected format",
            )

        # Before anything is converted: the split table has to survive the one
        # record that can contradict it.
        _check_lapse_units(data[fields.transactions], file_path, fields)

        transactions = [
            SchwabTransaction(transac, file_path, fields)
            for transac in data[fields.transactions]
            # Journal and Wire Transfer move cash between accounts.
            #
            # Lapse is the payroll side of a vest and duplicates its Deposit,
            # which already holds the net shares that reach the pool. Its own
            # counts are in the units of the day while its price is restated,
            # so pairing them puts a tenth of the shares in at the full price.
            #
            # Tax Withholding is deliberately not skipped: it is US tax at
            # source on dividends, and it is needed for SA106 and foreign tax
            # credit relief.
            if transac[fields.action] not in {"Journal", "Wire Transfer", "Lapse"}
        ]

        # A purchase whose every share went to tax acquired nothing. Keeping it
        # would offer the calculator an acquisition of zero shares, which it
        # rightly refuses; the tax on it is settled through payroll either way.
        transactions = [
            transaction
            for transaction in transactions
            if not (
                transaction.action == ActionType.STOCK_ACTIVITY
                and transaction.quantity == 0
            )
        ]

        # And after: each converted disposal against the acquisitions around it.
        _check_disposal_units(transactions, file_path)

        transactions.reverse()
        return list(transactions)
