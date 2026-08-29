"""Unit tests on schwab_equity_award_json.py."""

from __future__ import annotations

import datetime
from decimal import Decimal
import io
import json
import logging
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers import schwab_equity_award_json
from tests.utils import build_cmd, report_path, stderr_alerts

if TYPE_CHECKING:
    from cgt_calc.parsers.schwab_equity_award_json import SchwabAwardTransaction

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
) -> list[SchwabAwardTransaction]:
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


def test_schwab_json_invalid_json(tmp_path: Path) -> None:
    """Ensure malformed JSON raises ParsingError."""
    json_path = tmp_path / "invalid.json"
    json_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ParsingError, match="Could not parse content as JSON"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_missing_top_level_key(tmp_path: Path) -> None:
    """Ensure JSON missing expected top-level keys raises ParsingError."""
    json_path = tmp_path / "missing_keys.json"
    json_path.write_text(json.dumps({"someOtherKey": []}), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Expected top level field \(transactions, Transactions\) not found",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_transactions_not_a_list(tmp_path: Path) -> None:
    """Ensure non-list Transactions field raises ParsingError."""
    json_path = tmp_path / "not_a_list.json"
    json_path.write_text(json.dumps({"Transactions": "not-a-list"}), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"'Transactions' is not a list"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_buy_transaction_fails(tmp_path: Path) -> None:
    """Ensure loading a json with a buy transaction fails as unsupported (we expect Deposit)."""
    json_path = tmp_path / "buy_transaction.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Buy",
                "Symbol": "GOOG",
                "Description": "Bought shares",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError, match=r"Parsing for action Buy is not implemented!"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_unknown_action_fails(tmp_path: Path) -> None:
    """Ensure unknown actions in JSON raise ParsingError."""
    json_path = tmp_path / "unknown_action.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "UnknownAction",
                "Symbol": "GOOG",
                "Description": "Unknown",
                "Quantity": "10",
                "Amount": "$100.00",
                "FeesAndCommissions": None,
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"Unknown action: UnknownAction"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_missing_details_fails(tmp_path: Path) -> None:
    """Deposit transaction with missing TransactionDetails raises ParsingError."""
    json_path = tmp_path / "deposit_no_details.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": None,
                "FeesAndCommissions": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Expected a single Transaction Details for a Deposit, but found 0",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_multiple_details_fails(tmp_path: Path) -> None:
    """Deposit transaction with multiple TransactionDetails raises ParsingError."""
    json_path = tmp_path / "deposit_multi_details.json"
    detail = {
        "Details": {
            "AwardDate": "01/01/2020",
            "AwardId": "C123",
            "VestDate": "01/01/2023",
            "VestFairMarketValue": "$100.00",
        }
    }
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": None,
                "FeesAndCommissions": None,
                "TransactionDetails": [detail, detail],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Expected a single Transaction Details for a Deposit, but found 2",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_empty_and_null_amount(tmp_path: Path) -> None:
    """Deposit transaction amount is calculated when Amount is empty string or null."""
    json_path = tmp_path / "deposit_empty_amount.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "$150.00",
                        }
                    }
                ],
            },
            {
                "Date": "02/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "5",
                "Amount": None,
                "FeesAndCommissions": None,
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C124",
                            "VestDate": "02/01/2023",
                            "VestFairMarketValue": "$200.00",
                        }
                    }
                ],
            },
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 2
    # Note: load_from_file reverses the transactions list
    assert transactions[0].date == datetime.date(2023, 2, 1)
    assert transactions[0].amount == Decimal(1000)
    assert transactions[0].fees == Decimal(0)
    assert transactions[1].date == datetime.date(2023, 1, 1)
    assert transactions[1].amount == Decimal(1500)
    assert transactions[1].fees == Decimal(0)


@pytest.mark.parametrize(
    (
        "quantity_str",
        "amount_str",
        "fees_str",
        "transac_details",
        "expected_quantity",
        "expected_price",
    ),
    [
        pytest.param(
            "12.549",  # quantity_str
            "$1,254.40",  # amount_str
            "$0.50",  # fees_str
            [{"Details": {"SalePrice": "$100.00"}}],
            Decimal("12.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="non_integer_quantity",
        ),
        pytest.param(
            "12",  # quantity_str
            "$1,254.40",  # amount_str
            "$0.50",  # fees_str
            [
                {"Details": {"Shares": "3.549", "SalePrice": "$100.00"}},
                {"Details": {"Shares": "9", "SalePrice": "$100.00"}},
            ],
            Decimal("12.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_with_fractional_subtransaction_shares",
        ),
        pytest.param(
            "12",  # quantity_str
            "$1,200.00",  # amount_str
            "",  # fees_str
            [
                {"Details": {"Shares": "3", "SalePrice": "$100.00"}},
                {"Details": {"Shares": "9", "SalePrice": "$100.00"}},
            ],
            Decimal(12),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_with_integer_subtransaction_shares_exact",
        ),
        pytest.param(
            "12",  # quantity_str
            "$1,254.90",  # amount_str
            "",  # fees_str
            [
                {"Details": {"Shares": "3", "SalePrice": "$100.00"}},
                {"Details": {"Shares": "9", "SalePrice": "$100.00"}},
            ],
            Decimal("12.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_with_integer_subtransaction_shares_truncated",
        ),
        pytest.param(
            "10",  # quantity_str
            "$1,000.00",  # amount_str
            "",  # fees_str
            [{"Details": {"SalePrice": "$100.00"}}],
            Decimal(10),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_without_subtransaction_shares_exact",
        ),
        pytest.param(
            "10",  # quantity_str
            "$1,054.90",  # amount_str
            "",  # fees_str
            [{"Details": {"SalePrice": "$100.00"}}],
            Decimal("10.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_without_subtransaction_shares_truncated",
        ),
    ],
)
def test_schwab_json_sale_variations(
    tmp_path: Path,
    quantity_str: str,
    amount_str: str,
    fees_str: str,
    transac_details: list[dict[str, object]],
    expected_quantity: Decimal,
    expected_price: Decimal,
) -> None:
    """Test Sale parsing variations across integer/non-integer quantities and subtransaction shares."""
    json_path = tmp_path / "sale_variation.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": quantity_str,
                "Description": "Share Sale",
                "FeesAndCommissions": fees_str,
                "Amount": amount_str,
                "TransactionDetails": transac_details,
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == expected_quantity
    assert transactions[0].price == expected_price


def test_schwab_json_sale_missing_details_fails(tmp_path: Path) -> None:
    """Sale transaction with missing TransactionDetails raises ParsingError."""
    json_path = tmp_path / "sale_no_details.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Description": "Share Sale",
                "Quantity": "10",
                "Amount": "$1,000.00",
                "FeesAndCommissions": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"Expected transaction details for Sale"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_empty_vest_price_raises(tmp_path: Path) -> None:
    """Deposit with empty amount and empty VestFairMarketValue raises ParsingError."""
    json_path = tmp_path / "deposit_empty_price.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "",
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Missing or empty VestFairMarketValue for Deposit on 2023-01-01",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_sale_differing_subtransac_prices_fails(
    tmp_path: Path,
) -> None:
    """Sale with differing sub-transaction prices raises ParsingError."""
    json_path = tmp_path / "sale_differing_prices.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "20",
                "Description": "Share Sale",
                "FeesAndCommissions": "$0.02",
                "Amount": "$2,000.00",
                "TransactionDetails": [
                    {
                        "Details": {
                            "SalePrice": "$100.00",
                        }
                    },
                    {
                        "Details": {
                            "SalePrice": "$105.00",
                        }
                    },
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"Impossible to work out quantity of sale"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


@pytest.mark.parametrize(
    ("qty", "saleprice", "fees_and_commissions", "amount", "expected_fees"),
    [
        ("10", "$100.00", "", "$1,000.00", Decimal(0)),
        ("10", "$100.00", None, "$1,000.00", Decimal(0)),
        ("10", "$100.00", "$1.50", "$998.50", Decimal("1.50")),
    ],
)
def test_schwab_json_sale_fees_variations(
    tmp_path: Path,
    qty: str,
    saleprice: str,
    fees_and_commissions: str | None,
    amount: str,
    expected_fees: Decimal,
) -> None:
    """Test sale parsing with empty string, null, and populated fees."""
    json_path = tmp_path / "sale_fees.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": qty,
                "Description": "Share Sale",
                "FeesAndCommissions": fees_and_commissions,
                "Amount": amount,
                "TransactionDetails": [
                    {
                        "Details": {
                            "SalePrice": saleprice,
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].fees == expected_fees


def test_schwab_json_dividend_empty_amount_fails(tmp_path: Path) -> None:
    """Dividend transaction with empty Amount raises ParsingError."""
    json_path = tmp_path / "dividend_empty_amount.json"
    data = {
        "Transactions": [
            {
                "Date": "06/17/2024",
                "Action": "Dividend",
                "Symbol": "GOOG",
                "Description": "Credit",
                "FeesAndCommissions": "",
                "Amount": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Missing or empty Amount for Dividend on 2024-06-17",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_skipped_actions(tmp_path: Path) -> None:
    """Journal and Wire Transfer transactions are skipped during parsing."""
    json_path = tmp_path / "skipped_actions.json"
    data = {
        "Transactions": [
            {
                "Date": "09/01/2023",
                "Action": "Journal",
                "Symbol": "GOOG",
                "Quantity": None,
                "Description": "Journal To Account",
                "FeesAndCommissions": "",
                "Amount": "-$1,000.00",
                "TransactionDetails": [],
            },
            {
                "Date": "09/02/2023",
                "Action": "Wire Transfer",
                "Symbol": "GOOG",
                "Quantity": None,
                "Description": "Wire Transfer",
                "FeesAndCommissions": None,
                "Amount": "-$500.00",
                "TransactionDetails": [],
            },
            {
                "Date": "09/25/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Quantity": "10",
                "Description": "RS",
                "FeesAndCommissions": "",
                "Amount": None,
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "09/25/2023",
                            "VestFairMarketValue": "$130.00",
                        }
                    }
                ],
            },
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.STOCK_ACTIVITY


def test_schwab_json_v1_empty_fees(tmp_path: Path) -> None:
    """Test schema v1 with empty string and null fees."""
    json_path = tmp_path / "v1_empty_fees.json"
    data = {
        "transactions": [
            {
                "eventDate": "06/14/2022",
                "action": "Sale",
                "symbol": "GOOG",
                "quantity": "10",
                "description": "Sale",
                "totalCommissionsAndFees": "",
                "amount": "$1,000.00",
                "transactionDetails": [
                    {
                        "salePrice": "$100.00",
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].fees == Decimal(0)
    assert transactions[0].action == ActionType.SELL


def test_run_with_schwab_equity_award_json(request: pytest.FixtureRequest) -> None:
    """Run cgt-calc end-to-end with schwab-equity-award-json file."""
    input_file_base = "schwab_equity_award_v2"
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        f"tests/schwab/data/equity_award/{input_file_base}.json",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], (
        f"Run with example file {input_file_base}.json generated errors"
    )
    expected_file = (
        Path("tests")
        / "schwab"
        / "data"
        / "equity_award"
        / f"{input_file_base}-expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )
