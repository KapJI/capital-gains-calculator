"""Unit tests on schwab_equity_award_json.py."""

from __future__ import annotations

import datetime
from decimal import Decimal
import io
import logging
from pathlib import Path

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers import schwab_equity_award_json

# ruff: noqa: SLF001 "Private member accessed"


def test_decimal_from_str() -> None:
    """Test _decimal_from_str()."""
    assert schwab_equity_award_json._decimal_from_str("$123,456.23") == Decimal(
        "123456.23"
    )


def test_decimal_from_number_or_str_both() -> None:
    """Test _decimal_from_number_or_str() on float."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "123.45", "keySortValue": Decimal("67.89")}, "key"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_float_null() -> None:
    """Test _decimal_from_number_or_str() on None float."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "67.89", "keySortValue": None}, "key"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_none() -> None:
    """Test _decimal_from_number_or_str() with a value of None."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": None}, "key"
    ) == Decimal(0)


def test_decimal_from_number_or_str_empty_string() -> None:
    """Test _decimal_from_number_or_str() with an empty string value."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": ""}, "key"
    ) == Decimal(0)


def test_decimal_from_number_or_str_float_custom_suffix() -> None:
    """Test _decimal_from_number_or_str() on float.

    With a custom suffix.
    """
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"keyMySuffix": Decimal("67.89")}, "key", "MySuffix"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_default() -> None:
    """Test _decimal_from_number_or_str() with absent keys."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "123.45", "keySortValue": 67.89}, "otherkey"
    ) == Decimal(0)


def test_schwab_transaction_v1() -> None:
    """Test read_schwab_equity_award_json_transactions() on v1 data."""
    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            Path("tests/schwab/data/equity_award/schwab_equity_award_v1.json")
        )
    )

    assert transactions[0].date == datetime.date(2022, 4, 25)
    assert transactions[0].action == ActionType.STOCK_ACTIVITY
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal("67.2")
    assert transactions[0].price == Decimal("125.6445")
    assert transactions[0].fees == Decimal(0)
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    assert transactions[1].date == datetime.date(2022, 6, 14)
    assert transactions[1].action == ActionType.SELL
    assert transactions[1].quantity == Decimal("62.601495")
    assert transactions[1].price == Decimal("113.75")
    assert transactions[1].fees == Decimal("0.17")

    assert transactions[2].date == datetime.date(2022, 10, 25)
    assert transactions[2].action == ActionType.STOCK_ACTIVITY
    assert transactions[2].quantity == Decimal("10.45")
    assert transactions[2].price == Decimal("112.42")
    assert transactions[2].fees == Decimal(0)

    assert transactions[3].date == datetime.date(2022, 11, 16)
    assert transactions[3].action == ActionType.SELL
    assert transactions[3].quantity == Decimal("12.549")
    assert transactions[3].price is not None
    assert transactions[3].price.quantize(Decimal(".000001")).normalize() == Decimal(
        "2051.597737"
    )
    assert transactions[3].amount == Decimal(25745)
    assert transactions[3].fees == Decimal("0.50")


def test_schwab_transaction_v2() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2 data."""
    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            Path("tests/schwab/data/equity_award/schwab_equity_award_v2.json")
        )
    )

    i = 0
    assert transactions[i].date == datetime.date(2023, 4, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].quantity == Decimal("4.911")
    assert transactions[i].price == Decimal("106.78")
    assert transactions[i].fees == Decimal(0)

    i += 1
    assert transactions[i].date == datetime.date(2023, 4, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].quantity == Decimal("13.6")
    assert transactions[i].price == Decimal("106.78")
    assert transactions[i].fees == Decimal(0)

    i += 1
    assert transactions[i].date == datetime.date(2023, 8, 31)
    assert transactions[i].action == ActionType.SELL
    assert transactions[i].quantity == Decimal("14.40")
    assert transactions[i].price == Decimal("137.90")
    assert transactions[i].fees == Decimal("0.02")

    i += 1
    assert transactions[i].date == datetime.date(2023, 9, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].quantity == Decimal("4.911")
    assert transactions[i].price == Decimal("131.25")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2023, 9, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].quantity == Decimal("13.6")
    assert transactions[i].price == Decimal("131.25")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 6, 17)
    assert transactions[i].action == ActionType.DIVIDEND
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("74.62")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 6, 17)
    assert transactions[i].action == ActionType.DIVIDEND_TAX
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("-22.39")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 7, 12)
    assert transactions[i].action == ActionType.DIVIDEND_TAX
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("11.20")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"


def test_schwab_transaction_v2_rounding() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2_rounding data.

    This tests 13 vesting events with 7 shares each, which are then sold.
    """
    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            Path("tests/schwab/data/equity_award/schwab_equity_award_v2_rounding.json")
        )
    )

    assert transactions[0].date == datetime.date(2020, 4, 24)
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal(91)
    assert transactions[0].price == Decimal("102.4935")
    assert transactions[0].fees == Decimal("0.49")
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    num_vested = Decimal(0)
    for transaction_id in range(1, len(transactions)):
        assert transactions[transaction_id].date == (
            datetime.date(2020, 1, 1) + datetime.timedelta(days=transaction_id)
        )
        assert transactions[transaction_id].action == ActionType.STOCK_ACTIVITY
        assert transactions[transaction_id].quantity == Decimal(7)
        num_vested += transactions[transaction_id].quantity or Decimal(0)
        assert transactions[transaction_id].price == Decimal("123.45")
        assert transactions[transaction_id].fees == Decimal(0)
    assert num_vested == transactions[0].quantity


def test_split_multiplier_google_boundary() -> None:
    """GOOG counts are in current units from the day after the split."""
    assert (
        schwab_equity_award_json.split_multiplier("GOOG", datetime.date(2022, 7, 15))
        == 20
    )
    assert (
        schwab_equity_award_json.split_multiplier("GOOG", datetime.date(2022, 7, 16))
        == 1
    )


def test_split_multiplier_ignores_unknown_symbol() -> None:
    """A symbol with no split history is left alone rather than guessed at."""
    assert (
        schwab_equity_award_json.split_multiplier("AAPL", datetime.date(2014, 6, 8))
        == 1
    )
    assert (
        schwab_equity_award_json.split_multiplier(None, datetime.date(2022, 1, 1)) == 1
    )


def test_split_history_demands_a_price_floor_it_can_use() -> None:
    """An UNRELIABLE export is unusable without the price that gives units away."""
    with pytest.raises(ValueError, match="presplit_price_floor"):
        schwab_equity_award_json.SplitHistory(
            ((datetime.date(2022, 7, 16), 20),),
            schwab_equity_award_json.Restatement.UNRELIABLE,
        )

    with pytest.raises(ValueError, match="presplit_price_floor"):
        schwab_equity_award_json.SplitHistory(
            ((datetime.date(2021, 7, 20), 4),),
            schwab_equity_award_json.Restatement.ACQUISITIONS_ONLY,
            presplit_price_floor=Decimal(175),
        )


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("Buy", ActionType.BUY),
        ("Sale", ActionType.SELL),
        ("Sell", ActionType.SELL),
        ("Forced Quick Sell", ActionType.SELL),
        ("Forced Disbursement", ActionType.TRANSFER),
        ("Deposit", ActionType.STOCK_ACTIVITY),
        ("Qualified Dividend", ActionType.DIVIDEND),
        ("Cash Dividend", ActionType.DIVIDEND),
        ("Dividend", ActionType.DIVIDEND),
        ("MoneyLink Transfer", ActionType.TRANSFER),
        ("Cash In Lieu", ActionType.TRANSFER),
        ("Stock Plan Activity", ActionType.STOCK_ACTIVITY),
        ("NRA Tax Adj", ActionType.DIVIDEND_TAX),
        ("NRA Withholding", ActionType.DIVIDEND_TAX),
        ("Foreign Tax Paid", ActionType.DIVIDEND_TAX),
        ("Tax Reversal", ActionType.DIVIDEND_TAX),
        ("Tax Withholding", ActionType.DIVIDEND_TAX),
        ("ADR Mgmt Fee", ActionType.FEE),
        ("Adjustment", ActionType.ADJUSTMENT),
        ("IRS Withhold Adj", ActionType.ADJUSTMENT),
        ("Short Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Long Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Spin-off", ActionType.SPIN_OFF),
        ("Credit Interest", ActionType.INTEREST),
        ("Reinvest Shares", ActionType.REINVEST_SHARES),
        ("Reinvest Dividend", ActionType.REINVEST_DIVIDENDS),
        ("Wire Funds Received", ActionType.WIRE_FUNDS_RECEIVED),
        ("Gift", ActionType.UNCLASSIFIED_GIFT),
    ],
)
def test_action_from_str(label: str, action: ActionType) -> None:
    """Map every known Schwab equity award action label."""
    assert (
        schwab_equity_award_json.action_from_str(label, Path("awards.json")) == action
    )


def test_action_from_str_unknown() -> None:
    """Raise on unknown action labels."""
    with pytest.raises(ParsingError, match="Unknown action"):
        schwab_equity_award_json.action_from_str("Dance", Path("awards.json"))


def _read_json(
    content: str,
) -> list[schwab_equity_award_json.SchwabTransaction]:
    """Parse Schwab equity award JSON from a string."""
    parser = schwab_equity_award_json.SchwabEquityAwardsJSONParser
    return parser.read_transactions(io.StringIO(content), Path("awards.json"))


def test_read_transactions_invalid_json() -> None:
    """Raise on malformed JSON."""
    with pytest.raises(ParsingError, match="Could not parse content as JSON"):
        _read_json("{not json")


def test_read_transactions_unknown_top_level_field() -> None:
    """Raise when no known top level field is present."""
    with pytest.raises(ParsingError, match="Expected top level field"):
        _read_json('{"Unknown": []}')


def test_read_transactions_transactions_not_a_list() -> None:
    """Raise when the transactions field is not a list."""
    with pytest.raises(ParsingError, match="is not a list"):
        _read_json('{"Transactions": {}}')


def test_unknown_symbol_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Warn about symbols without known split history."""
    content = (
        '{"Transactions": [{"Date": "01/15/2023", "Action": "Dividend",'
        ' "Symbol": "ZZZ", "Description": "Div", "Quantity": null,'
        ' "Amount": "$10.00", "FeesAndCommissions": null,'
        ' "TransactionDetails": []}]}'
    )

    with caplog.at_level(logging.WARNING):
        transactions = _read_json(content)

    assert "check the share counts" in caplog.text
    assert transactions[0].action == ActionType.DIVIDEND
    assert transactions[0].amount == Decimal(10)


def test_forced_disbursement_parses_as_transfer() -> None:
    """Parse the Forced Disbursement sample from issue #488."""
    content = (
        '{"Transactions": [{"Date": "03/31/2015", "Action": "Forced Disbursement",'
        ' "Symbol": "GOOG", "Quantity": null, "Description": "Debit",'
        ' "FeesAndCommissions": null, "DisbursementElection": null,'
        ' "Amount": "-$2,490.13", "TransactionDetails": []}]}'
    )

    transactions = _read_json(content)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal("-2490.13")
    assert transaction.date == datetime.date(2015, 3, 31)
    assert transaction.quantity is None or transaction.quantity == 0


def test_gift_parses_as_gift() -> None:
    """Parse a gift of shares: quantity moves, no money does."""
    content = (
        '{"Transactions": [{"Date": "11/20/2022", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transactions = _read_json(content)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.UNCLASSIFIED_GIFT
    assert transaction.symbol == "GOOG"
    assert transaction.quantity == Decimal(2)
    assert transaction.date == datetime.date(2022, 11, 20)
    assert transaction.price is None


def test_gift_before_a_split_is_restated_like_a_sale() -> None:
    """Shares leave in the units of their own day, gift or sale alike.

    NVDA exports restate acquisitions and nothing else, so a 2022 gift is
    printed in pre-split units and has to be converted like a disposal.
    """
    content = (
        '{"Transactions": [{"Date": "11/20/2022", "Action": "Gift",'
        ' "Symbol": "NVDA", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transactions = _read_json(content)

    # 4:1 in 2021 is already reflected; the 10:1 of June 2024 is not.
    assert transactions[0].quantity == Decimal(20)


def test_ambiguous_gift_before_a_split_records_both_readings() -> None:
    """Where only the price can date the units, a gift has nothing to go on.

    GOOG exports restate some records and not others, and a gift carries no
    price, so the count could be either and guessing is wrong by the whole
    multiplier. Both readings are kept for the calculator to put to the user.
    """
    content = (
        '{"Transactions": [{"Date": "05/02/2022", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transaction = _read_json(content)[0]

    assert transaction.quantity == Decimal(2)
    assert transaction.ambiguous_quantity == Decimal(40)


def test_gift_after_every_split_is_left_alone() -> None:
    """Nothing to convert once the counts are already current."""
    content = (
        '{"Transactions": [{"Date": "11/20/2024", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transaction = _read_json(content)[0]

    assert transaction.quantity == Decimal(2)
    # Nothing to be unsure about once the counts are current.
    assert transaction.ambiguous_quantity is None


def test_gift_with_money_raises() -> None:
    """A gift row carrying money is not the row this parser assumes."""
    content = (
        '{"Transactions": [{"Date": "11/20/2022", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": "$100.00",'
        ' "TransactionDetails": []}]}'
    )

    with pytest.raises(ParsingError, match="Unexpected amount or fees"):
        _read_json(content)


def test_v2_sale_quantity_from_lot_shares() -> None:
    """Sum fractional lot Shares for a v2 sale whose lots have different prices.

    Regression test: the lot membership check used the lowercase v1 key
    "shares", so v2 sales never took the share-sum path and fell through to
    the sale-price path, which raises when lots disagree on price.
    """
    content = (
        '{"Transactions": [{"Date": "08/31/2023", "Action": "Sale",'
        ' "Symbol": "GOOG", "Quantity": "10", "Description": "Share Sale",'
        ' "FeesAndCommissions": "$0.00", "Amount": "$1,045.00",'
        ' "TransactionDetails": ['
        '{"Details": {"Shares": "5.5", "SalePrice": "$100.00"}},'
        '{"Details": {"Shares": "4.5", "SalePrice": "$110.00"}}'
        "]}]}"
    )

    transactions = _read_json(content)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal("104.5")


def test_unimplemented_action_raises() -> None:
    """Raise on actions the parser does not implement."""
    content = (
        '{"Transactions": [{"Date": "01/15/2023", "Action": "Buy",'
        ' "Symbol": "GOOG", "Description": "Buy", "Quantity": "1",'
        ' "Amount": "$10.00", "FeesAndCommissions": null,'
        ' "TransactionDetails": []}]}'
    )

    with pytest.raises(ParsingError, match="is not implemented"):
        _read_json(content)


def test_detail_counts_multiplier() -> None:
    """Compose the ratios of every split after the given date."""
    multiplier = schwab_equity_award_json._detail_counts_multiplier

    # NVDA restates acquisitions only and split 4:1 on 2021-07-20
    # and 10:1 on 2024-06-10.
    assert multiplier("NVDA", datetime.date(2021, 7, 19)) == 40
    assert multiplier("NVDA", datetime.date(2021, 7, 20)) == 10
    assert multiplier("NVDA", datetime.date(2024, 6, 10)) == 1
    # GOOG's export does not restate uniformly, so counts inside the
    # details are already comparable and need no multiplier.
    assert multiplier("GOOG", datetime.date(2020, 1, 1)) == 1


def test_lot_shares_and_price_helpers() -> None:
    """Lot helpers return None when lots are incomplete or disagree."""
    names = schwab_equity_award_json.FieldNames(2)

    no_shares = {"TransactionDetails": [{"Details": {"SalePrice": "$10.00"}}]}
    assert schwab_equity_award_json._lot_share_sum(no_shares, names) is None

    no_price = {"TransactionDetails": [{"Details": {"Shares": "1.5"}}]}
    assert schwab_equity_award_json._lot_price(no_price, names) is None

    different_prices = {
        "TransactionDetails": [
            {"Details": {"SalePrice": "$10.00"}},
            {"Details": {"SalePrice": "$11.00"}},
        ]
    }
    assert schwab_equity_award_json._lot_price(different_prices, names) is None
