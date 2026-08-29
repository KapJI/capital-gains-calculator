"""Trading 212 exports a share reorganisation as two rows.

`Stock split close` states the complete position before the event and
`Stock split open` the complete position after it, in either order and
sometimes in different files. Neither row on its own says what happened, and
neither is a purchase or a sale: the totals either side are the same money,
which is what makes the pair checkable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING, TypedDict, Unpack

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.trading212 import (
    SPLIT_CLOSE_ACTION,
    SPLIT_OPEN_ACTION,
    Trading212Column,
    Trading212Parser,
    Trading212Transaction,
)
from cgt_calc.stock_splits import StockSplitTransaction, UnresolvedRatio
from tests.utils import build_cmd, report_path, stderr_alerts

from .test_trading212 import HEADER_2026, HEADER_2026_UTC, _make_row, _write_csv

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cgt_calc.model import BrokerTransaction

# A one-for-nine consolidation of a whole share, priced so that the two rows
# state the same money. The counts are as Trading 212 prints them: ten
# decimal places, but granular to 1e-8, so their quotient is not 1/9.
CONSOLIDATION_BEFORE = "1.0000000000"
CONSOLIDATION_AFTER = "0.1111111100"
PRICE_BEFORE = "90.00"
PRICE_AFTER = "810.00"
TOTAL = "90.00"

CLOSED_AT = "2026-02-02 07:44:13"
OPENED_AT = "2026-02-02 07:44:13"
EVENT_DAY = date(2026, 2, 2)


class HalfOptions(TypedDict, total=False):
    """What a caller may change about one reorganisation row."""

    time: str
    shares: str
    price: str
    total: str
    ticker: str
    isin: str
    price_currency: str
    rate: str
    currency: str
    transaction_id: str
    time_column: Trading212Column
    extra: Mapping[str | Trading212Column, str]


def split_row(
    header: list[str],
    action: str,
    *,
    time: str = CLOSED_AT,
    shares: str = CONSOLIDATION_BEFORE,
    price: str = PRICE_BEFORE,
    total: str = TOTAL,
    ticker: str = "FOO",
    isin: str = "US0000000002",
    price_currency: str = "GBP",
    rate: str = "",
    currency: str = "GBP",
    transaction_id: str | None = None,
    time_column: Trading212Column = Trading212Column.TIME,
    extra: Mapping[str | Trading212Column, str] | None = None,
) -> list[str]:
    """Build one half of a reorganisation as the export writes it."""
    overrides: dict[str | Trading212Column, str] = {
        Trading212Column.ACTION: action,
        time_column: time,
        Trading212Column.ISIN: isin,
        Trading212Column.TICKER: ticker,
        Trading212Column.NAME: "Foo Inc",
        Trading212Column.NO_OF_SHARES: shares,
        Trading212Column.PRICE_PER_SHARE: price,
        Trading212Column.CURRENCY_PRICE_PER_SHARE: price_currency,
        Trading212Column.EXCHANGE_RATE: rate,
        Trading212Column.TOTAL: total,
        Trading212Column.CURRENCY_TOTAL: currency,
        Trading212Column.TRANSACTION_ID: (
            transaction_id if transaction_id is not None else f"{action}-1"
        ),
    }
    overrides.update(extra or {})
    return _make_row(header, overrides)


def close_row(
    header: list[str] = HEADER_2026, **options: Unpack[HalfOptions]
) -> list[str]:
    """Build the row stating the position before the event."""
    options.setdefault("time", CLOSED_AT)
    options.setdefault("shares", CONSOLIDATION_BEFORE)
    options.setdefault("price", PRICE_BEFORE)
    return split_row(header, SPLIT_CLOSE_ACTION, **options)


def open_row(
    header: list[str] = HEADER_2026, **options: Unpack[HalfOptions]
) -> list[str]:
    """Build the row stating the position after the event."""
    options.setdefault("time", OPENED_AT)
    options.setdefault("shares", CONSOLIDATION_AFTER)
    options.setdefault("price", PRICE_AFTER)
    return split_row(header, SPLIT_OPEN_ACTION, **options)


def trade_row(
    header: list[str] = HEADER_2026,
    *,
    action: str = "Market buy",
    time: str = "2026-02-02 06:00:00",
    shares: str = "1.0000000000",
    price: str = "90.00",
    total: str = "90.00",
    ticker: str = "FOO",
    transaction_id: str = "trade-1",
    extra: Mapping[str | Trading212Column, str] | None = None,
) -> list[str]:
    """Build an ordinary trade in the same export."""
    return _make_row(
        header,
        {
            **(extra or {}),
            Trading212Column.ACTION: action,
            Trading212Column.TIME: time,
            Trading212Column.ISIN: "US0000000002",
            Trading212Column.TICKER: ticker,
            Trading212Column.NAME: "Foo Inc",
            Trading212Column.NO_OF_SHARES: shares,
            Trading212Column.PRICE_PER_SHARE: price,
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
            Trading212Column.TOTAL: total,
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_ID: transaction_id,
        },
    )


def load(
    tmp_path: Path,
    files: Mapping[str, list[list[str]]],
) -> list[BrokerTransaction]:
    """Load one directory of exports, which is one declared account."""
    folder = tmp_path / "inputs"
    folder.mkdir(exist_ok=True)
    for name, rows in files.items():
        _write_csv(folder / name, rows)
    return Trading212Parser().load_from_dir(folder)


def load_pair(
    tmp_path: Path,
    rows: list[list[str]],
    header: list[str] = HEADER_2026,
) -> StockSplitTransaction:
    """Load one export and return the single reorganisation it states."""
    (event,) = load(tmp_path, {"export.csv": [header, *rows]})
    assert isinstance(event, StockSplitTransaction)
    return event


# ===== the pair, in either order =====


@pytest.mark.parametrize("order", ["close first", "open first"])
def test_the_two_rows_combine_whichever_order_they_are_written_in(
    tmp_path: Path, order: str
) -> None:
    """Trading 212 writes them both ways, and the result is the same event."""
    rows = [close_row(), open_row()]
    if order == "open first":
        rows.reverse()

    event = load_pair(tmp_path, rows)

    assert event.action is ActionType.STOCK_SPLIT
    assert event.symbol == "FOO"
    assert event.date == EVENT_DAY
    assert event.event.before_quantity == Decimal(CONSOLIDATION_BEFORE)
    assert event.event.after_quantity == Decimal(CONSOLIDATION_AFTER)
    assert event.event.ratio == Fraction(1, 9)
    # Nothing was bought, sold or paid for.
    assert event.amount == Decimal(0)
    assert event.fees == Decimal(0)


def test_a_close_row_can_be_the_first_row_in_the_file(tmp_path: Path) -> None:
    """Pairing does not lean on the row before it.

    Reading the close row's partner off the previous transaction breaks on
    the first row of a file, and quietly picks the wrong row everywhere else.
    """
    transactions = load(
        tmp_path,
        {"export.csv": [HEADER_2026, close_row(), trade_row(), open_row()]},
    )
    (event,) = (t for t in transactions if isinstance(t, StockSplitTransaction))
    assert event.event.ratio == Fraction(1, 9)


def test_a_forward_split_multiplies_the_count(tmp_path: Path) -> None:
    """A ratio above one is the same shape, read the other way."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="0.0638781600", price="1000.00", total="63.88"),
            open_row(shares="0.6387816000", price="100.00", total="63.88"),
        ],
    )
    assert event.event.ratio == Fraction(10)
    assert event.quantity == Decimal("0.5749034400")


def test_a_ratio_that_is_not_a_whole_number_is_recovered(tmp_path: Path) -> None:
    """Unilever's nine-for-eight consolidation, as exported."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="4.2874920800", price="10.00", total="42.87"),
            open_row(shares="3.8111040700", price="11.25", total="42.87"),
        ],
    )
    assert event.event.ratio == Fraction(8, 9)


# ===== when the rows were stamped =====


@pytest.mark.parametrize(
    ("close_time", "open_time"),
    [
        ("2026-02-02 07:44:13", "2026-02-02 07:44:13"),
        ("2026-02-02 07:44:13.000", "2026-02-02 07:44:13.009"),
        ("2026-02-02 07:44:13", "2026-02-02 07:44:14"),
    ],
    ids=["identical", "milliseconds apart", "at the bound"],
)
def test_halves_stamped_close_enough_together_pair(
    tmp_path: Path, close_time: str, open_time: str
) -> None:
    """The halves are stamped at one instant, or all but."""
    event = load_pair(tmp_path, [close_row(time=close_time), open_row(time=open_time)])
    assert event.event.ratio == Fraction(1, 9)


@pytest.mark.parametrize(
    ("close_time", "open_time"),
    [
        ("2026-02-02 07:44:13", "2026-02-02 07:44:14.001"),
        ("2026-02-02 07:44:14", "2026-02-02 07:44:13"),
    ],
    ids=["past the bound", "open before close"],
)
def test_halves_stamped_too_far_apart_do_not_pair(
    tmp_path: Path, close_time: str, open_time: str
) -> None:
    """A position cannot reopen before it closed, or a second later."""
    with pytest.raises(ParsingError, match="not the two halves of one event"):
        load_pair(tmp_path, [close_row(time=close_time), open_row(time=open_time)])


def test_a_trade_between_the_two_halves_is_refused(tmp_path: Path) -> None:
    """Its count is in neither unit system, and nothing says which."""
    with pytest.raises(ParsingError, match="stamped between the two halves"):
        load_pair(
            tmp_path,
            [
                close_row(time="2026-02-02 07:44:13"),
                trade_row(time="2026-02-02 07:44:13.500"),
                open_row(time="2026-02-02 07:44:14"),
            ],
        )


def test_a_trade_outside_the_two_halves_is_left_alone(tmp_path: Path) -> None:
    """Only a row stamped between them is ambiguous."""
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                trade_row(time="2026-02-02 06:00:00"),
                close_row(time="2026-02-02 07:44:13"),
                open_row(time="2026-02-02 07:44:14"),
            ]
        },
    )
    assert len(transactions) == 2


def test_the_current_header_and_an_offset_timestamp_pair(tmp_path: Path) -> None:
    """Trading 212 renamed the column and spelled the zone out."""
    event = load_pair(
        tmp_path,
        [
            close_row(
                HEADER_2026_UTC,
                time="2026-02-02T07:44:13+00:00",
                time_column=Trading212Column.TIME_UTC,
            ),
            open_row(
                HEADER_2026_UTC,
                time="2026-02-02T07:44:13+00:00",
                time_column=Trading212Column.TIME_UTC,
            ),
        ],
        HEADER_2026_UTC,
    )
    assert event.date == EVENT_DAY
    assert event.event.instants == (
        datetime(2026, 2, 2, 7, 44, 13, tzinfo=UTC),
        datetime(2026, 2, 2, 7, 44, 13, tzinfo=UTC),
    )


def test_a_late_evening_event_lands_on_the_uk_date(tmp_path: Path) -> None:
    """British Summer Time is an hour ahead, so 23:30 UTC is the next day."""
    event = load_pair(
        tmp_path,
        [
            close_row(time="2026-06-01 23:30:00"),
            open_row(time="2026-06-01 23:30:00"),
        ],
    )
    assert event.date == date(2026, 6, 2)


def test_an_event_at_the_clocks_going_forward_keeps_its_uk_date(
    tmp_path: Path,
) -> None:
    """01:00 UTC on the last Sunday in March is 02:00 in London, same day."""
    event = load_pair(
        tmp_path,
        [
            close_row(time="2026-03-29 01:00:00"),
            open_row(time="2026-03-29 01:00:00"),
        ],
    )
    assert event.date == date(2026, 3, 29)


# ===== more than one file, and more than one account =====


def test_the_two_halves_may_be_in_different_files(tmp_path: Path) -> None:
    """Exports are cut by date range, and an event can fall on the seam.

    Pairing has to see the whole directory: the per-file pass would reject a
    half whose partner is in the next file.
    """
    transactions = load(
        tmp_path,
        {
            "2026-01.csv": [HEADER_2026, close_row()],
            "2026-02.csv": [HEADER_2026, open_row()],
        },
    )
    (event,) = transactions
    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_halves_in_two_declared_accounts_do_not_pair(tmp_path: Path) -> None:
    """Passing a directory is the user's declaration of one account.

    Nothing in the CSV identifies the account, so two separately configured
    inputs are two accounts and their rows are not two halves of one event.
    """
    first = tmp_path / "first"
    first.mkdir()
    _write_csv(first / "export.csv", [HEADER_2026, close_row()])
    second = tmp_path / "second"
    second.mkdir()
    _write_csv(second / "export.csv", [HEADER_2026, open_row()])

    with pytest.raises(ParsingError, match="once each"):
        Trading212Parser().load_from_dir(first)


def test_a_pair_reported_by_two_overlapping_exports_is_one_event(
    tmp_path: Path,
) -> None:
    """Consecutive downloads cover the same days and repeat the same rows."""
    transactions = load(
        tmp_path,
        {
            "2026-01.csv": [HEADER_2026, close_row(), open_row()],
            "2026-02.csv": [HEADER_2026, close_row(), open_row()],
        },
    )
    (event,) = transactions
    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_two_rows_with_one_id_that_disagree_are_refused(tmp_path: Path) -> None:
    """One id is one transaction, so there is no telling which this is."""
    with pytest.raises(ParsingError, match=re.escape("disagree about No. of shares")):
        load(
            tmp_path,
            {
                "2026-01.csv": [HEADER_2026, trade_row(transaction_id="same")],
                "2026-02.csv": [
                    HEADER_2026,
                    trade_row(transaction_id="same", shares="2.0000000000"),
                ],
            },
        )


def test_rows_without_an_id_are_all_kept(tmp_path: Path) -> None:
    """A blank id identifies nothing, so two events can look identical.

    Collapsing them on whole-row equality would lose a real transaction.
    """
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                trade_row(transaction_id="", time="2026-01-05 09:00:00"),
                trade_row(transaction_id="", time="2026-01-05 09:00:00"),
            ]
        },
    )
    assert len(transactions) == 2


def test_more_than_one_reorganisation_in_a_day_is_refused(
    tmp_path: Path,
) -> None:
    """One security is restated once a day, or nothing here can be proved."""
    with pytest.raises(ParsingError, match="has 2 Stock split close"):
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(transaction_id=""),
                    close_row(transaction_id=""),
                    open_row(transaction_id=""),
                    open_row(transaction_id=""),
                ]
            },
        )


# ===== a half that does not pair =====


@pytest.mark.parametrize("keep", ["close", "open"])
def test_a_half_on_its_own_is_refused(tmp_path: Path, keep: str) -> None:
    """One row says nothing about what happened to the holding."""
    rows = [close_row()] if keep == "close" else [open_row()]
    with pytest.raises(ParsingError, match="once each"):
        load_pair(tmp_path, rows)


@pytest.mark.parametrize(
    ("other_half", "message"),
    [
        # A different security is a different holding, so the two rows are
        # never brought together to be compared at all.
        (open_row(ticker="BAR"), "once each"),
        (open_row(price_currency="USD", rate="1.25"), "once each"),
        (open_row(currency="USD", rate="1.25"), "once each"),
        # One ticker under two identifiers is a reorganisation that changes
        # the security as well, which needs relationship data the export does
        # not carry.
        (open_row(isin="US0000000010"), "changes the ISIN"),
    ],
    ids=["ticker", "price currency", "account currency", "isin"],
)
def test_halves_that_describe_different_things_do_not_pair(
    tmp_path: Path, other_half: list[str], message: str
) -> None:
    """A reorganisation restates one holding, in one currency."""
    with pytest.raises(ParsingError, match=message):
        load_pair(tmp_path, [close_row(), other_half])


def test_halves_whose_totals_disagree_do_not_pair(tmp_path: Path) -> None:
    """The two rows are the same money; that is what makes them a pair."""
    with pytest.raises(ParsingError, match="not the two halves of one event"):
        load_pair(tmp_path, [close_row(), open_row(total="95.00")])


def test_halves_whose_positions_are_worth_different_amounts_do_not_pair(
    tmp_path: Path,
) -> None:
    """A reorganisation preserves the value of the position."""
    with pytest.raises(ParsingError, match="not the two halves of one event"):
        load_pair(tmp_path, [close_row(), open_row(price="900.00")])


# ===== the fields a reorganisation row must and must not carry =====


@pytest.mark.parametrize("shares", ["", "0.0000000000", "-1.0000000000"])
def test_a_half_without_a_positive_share_count_is_refused(
    tmp_path: Path, shares: str
) -> None:
    """The row states a complete position, so it states a real count."""
    with pytest.raises(ParsingError, match="needs a positive share count"):
        load_pair(tmp_path, [close_row(shares=shares), open_row()])


def test_a_half_with_an_unreadable_share_count_is_refused(tmp_path: Path) -> None:
    """A value that is not a number is a parsing failure with its row."""
    with pytest.raises(ParsingError, match="row 2") as error:
        load_pair(tmp_path, [close_row(shares="lots"), open_row()])
    assert "No. of shares" in str(error.value)


@pytest.mark.parametrize(
    ("half", "column"),
    [
        (close_row(shares="NaN"), "No. of shares"),
        (close_row(shares="Infinity"), "No. of shares"),
        (close_row(price="NaN"), "Price / share"),
        (close_row(price="Infinity"), "Price / share"),
        (close_row(total="NaN"), "Total"),
        (close_row(total="Infinity"), "Total"),
        (close_row(rate="NaN"), "Exchange rate"),
        (close_row(rate="Infinity"), "Exchange rate"),
    ],
)
def test_a_half_with_a_non_finite_field_is_refused(
    tmp_path: Path, half: list[str], column: str
) -> None:
    """Refuse a cell that parses as a decimal but is not an amount.

    "NaN" and "Infinity" are refused with the row they came from rather than
    at the first arithmetic that touches them, which raises a bare decimal
    exception carrying no context at all.
    """
    with pytest.raises(ParsingError, match=re.escape(f"Invalid decimal in {column}")):
        load_pair(tmp_path, [half, open_row()])


@pytest.mark.parametrize("price", ["", "0.00", "-1.00"])
def test_a_half_without_a_positive_price_is_refused(tmp_path: Path, price: str) -> None:
    """The price is what makes the stated value checkable."""
    with pytest.raises(ParsingError, match="needs a positive price per share"):
        load_pair(tmp_path, [close_row(price=price), open_row()])


@pytest.mark.parametrize("total", ["", "-1.00"])
def test_a_half_without_a_usable_total_is_refused(tmp_path: Path, total: str) -> None:
    """A position has a value, even if it rounds to nothing."""
    with pytest.raises(ParsingError, match="needs a total of zero or more"):
        load_pair(tmp_path, [close_row(total=total), open_row(total=total)])


def test_a_position_worth_less_than_a_penny_is_allowed(tmp_path: Path) -> None:
    """A small enough holding rounds to zero in the account currency."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="0.0000090000", price="0.90", total="0.00"),
            open_row(shares="0.0000010000", price="8.10", total="0.00"),
        ],
    )
    assert event.event.before_quantity == Decimal("0.0000090000")
    assert event.event.after_quantity == Decimal("0.0000010000")


def test_a_blank_exchange_rate_is_a_rate_of_one_in_one_currency(
    tmp_path: Path,
) -> None:
    """Nothing to convert when the price and the total are in one currency."""
    event = load_pair(tmp_path, [close_row(rate=""), open_row(rate="")])
    assert event.event.ratio == Fraction(1, 9)


def test_a_blank_exchange_rate_across_currencies_is_refused(
    tmp_path: Path,
) -> None:
    """Without a rate the stated value cannot be checked at all."""
    with pytest.raises(ParsingError, match="states no exchange rate"):
        load_pair(
            tmp_path,
            [
                close_row(price_currency="USD", rate=""),
                open_row(price_currency="USD", rate=""),
            ],
        )


@pytest.mark.parametrize("rate", ["0", "-1.25"])
def test_a_half_with_an_impossible_exchange_rate_is_refused(
    tmp_path: Path, rate: str
) -> None:
    """A rate divides the price, so it has to be a positive number."""
    with pytest.raises(ParsingError, match="needs a positive exchange rate"):
        load_pair(tmp_path, [close_row(rate=rate), open_row(rate=rate)])


def test_each_half_is_converted_at_its_own_rate(tmp_path: Path) -> None:
    """The two halves can carry different rates, and both are needed.

    Trading 212 derives both rows from one account-currency total, so the
    per-row rates are exactly what makes the two values agree. Swapping them
    inflates the disagreement a thousandfold.
    """
    event = load_pair(
        tmp_path,
        [
            close_row(price_currency="USD", price="112.50", rate="1.25"),
            open_row(price_currency="USD", price="1012.50", rate="1.25"),
        ],
    )
    assert event.event.ratio == Fraction(1, 9)


@pytest.mark.parametrize(
    ("column", "currency_column"),
    [
        (Trading212Column.RESULT, Trading212Column.CURRENCY_RESULT),
        (Trading212Column.WITHHOLDING_TAX, Trading212Column.CURRENCY_WITHHOLDING_TAX),
        (Trading212Column.TRANSACTION_FEE, Trading212Column.CURRENCY_TRANSACTION_FEE),
        (
            Trading212Column.STAMP_DUTY_RESERVE_TAX,
            Trading212Column.CURRENCY_STAMP_DUTY_RESERVE_TAX,
        ),
        (
            Trading212Column.CURRENCY_CONVERSION_FEE,
            Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE,
        ),
        (
            Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT,
            Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT,
        ),
    ],
)
def test_a_half_that_moves_money_is_refused(
    tmp_path: Path,
    column: Trading212Column,
    currency_column: Trading212Column,
) -> None:
    """A reorganisation is not a disposal, so nothing is realised or paid.

    A cash leg alongside a consolidation can be a capital distribution, and
    that is a part disposal rather than a no-disposal reorganisation.
    """
    with pytest.raises(ParsingError, match="moves no money"):
        load_pair(
            tmp_path,
            [close_row(extra={column: "1.23", currency_column: "GBP"}), open_row()],
        )


def test_the_zeroes_a_real_export_writes_are_accepted(tmp_path: Path) -> None:
    """The close row states a result of 0.00 and the open row leaves it blank."""
    event = load_pair(
        tmp_path,
        [
            close_row(
                extra={
                    Trading212Column.RESULT: "0.00",
                    Trading212Column.CURRENCY_RESULT: "GBP",
                }
            ),
            open_row(),
        ],
    )
    assert event.event.ratio == Fraction(1, 9)


# ===== a ratio the counts do not single out =====


def test_a_ratio_the_counts_leave_ambiguous_still_parses(tmp_path: Path) -> None:
    """Whether the ratio is needed is not knowable while reading the file.

    A holding the export states in full is replaced outright and needs no
    ratio, so refusing here would reject a whole export for a number nothing
    asked for. The calculator refuses where the ratio is used.
    """
    event = load_pair(
        tmp_path,
        [
            close_row(shares="0.0001000000", price="900000.00", total="90.00"),
            open_row(shares="0.0000066700", price="13493253.37", total="90.00"),
        ],
    )
    assert isinstance(event.event.ratio, UnresolvedRatio)
    assert event.event.ratio.reason == "multiple"
    assert len(event.event.ratio.samples) == 2


def test_a_ratio_outside_the_supported_bound_still_parses(tmp_path: Path) -> None:
    """A ratio nothing supports is carried as unresolved, not guessed."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="5000.0000000000", price="0.018", total="90.00"),
            open_row(shares="1.0000000000", price="90.00", total="90.00"),
        ],
    )
    assert isinstance(event.event.ratio, UnresolvedRatio)
    assert event.event.ratio.reason == "none"


def test_a_full_run_over_a_split_and_a_consolidation(
    request: pytest.FixtureRequest,
) -> None:
    """Run the tool over an export holding both kinds of reorganisation.

    FOO is split ten for one and BAR consolidated nine for one, with the two
    halves of the FOO event in different exports. Both are in the current
    header with offset-aware timestamps, and the BAR halves are stamped nine
    milliseconds apart. The figures pin the whole path: the counts either
    side, the pooled cost carried through unchanged, the gain on a later
    disposal, and a cash balance the reorganisations leave alone even though
    their rows state a total.
    """
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/splits/inputs/",
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
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests") / "trading212" / "data" / "splits" / "expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_a_full_run_renders_each_reorganisation(tmp_path: Path) -> None:
    """The report shows the event itself, not a purchase of free shares."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/splits/inputs/",
        "--output",
        str(tmp_path / "report"),
        keep_tex=True,
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")

    assert (
        "Share reorganisation: 100 units of FOO restated as 1000 (ratio 10)" in source
    )
    assert "Share reorganisation: 90 units of BAR restated as 10 (ratio 1/9)" in source
    assert "Neither a disposal nor an acquisition" in source
    # The pooled cost is carried across the event untouched.
    assert "Stated as 100 units before the event and 1000 after." in source
    # A reorganisation is never rendered as an acquisition.
    assert "Acquisition 3" not in source


def test_a_file_loaded_on_its_own_is_a_boundary_that_pairs(tmp_path: Path) -> None:
    """One file passed on its own is one declared account, so it finalizes.

    Pairing runs once per boundary. A directory is one, and so is a single
    file, which would otherwise hand the calculator two raw halves.
    """
    export = tmp_path / "export.csv"
    _write_csv(export, [HEADER_2026, close_row(), open_row()])

    (event,) = Trading212Parser().load_from_file(export)

    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_two_rows_with_one_id_differing_only_in_an_unread_column_are_refused(
    tmp_path: Path,
) -> None:
    """Every column both exports carry is compared, not just the read ones.

    A summary of the fields the parser happens to use lets two rows that
    differ only in one it ignores collapse into one transaction.
    """
    with pytest.raises(ParsingError, match="disagree about Merchant name"):
        load(
            tmp_path,
            {
                "1.csv": [HEADER_2026, trade_row(transaction_id="same")],
                "2.csv": [
                    HEADER_2026,
                    trade_row(
                        transaction_id="same",
                        extra={Trading212Column.MERCHANT_NAME: "Somewhere else"},
                    ),
                ],
            },
        )


def test_a_column_only_one_export_carries_is_not_a_disagreement(
    tmp_path: Path,
) -> None:
    """Trading 212 has changed its columns more than once.

    A column one export has and the other does not says nothing about
    whether the two rows are the same transaction.
    """
    older = [column for column in HEADER_2026 if column != "Merchant name"]
    transactions = load(
        tmp_path,
        {
            "1.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "2.csv": [older, trade_row(older, transaction_id="same")],
        },
    )
    assert len(transactions) == 1


def test_the_first_copy_read_is_the_one_kept(tmp_path: Path) -> None:
    """Deterministic: globbed in sorted order, read in row order."""
    transactions = load(
        tmp_path,
        {
            "1.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "2.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "3.csv": [HEADER_2026, trade_row(transaction_id="same")],
        },
    )
    (kept,) = transactions
    assert isinstance(kept, Trading212Transaction)
    assert kept.source is not None
    assert kept.source.file is not None
    assert kept.source.file.name == "1.csv"


def test_a_pair_in_two_overlapping_exports_collapses_before_pairing(
    tmp_path: Path,
) -> None:
    """Both copies of both halves are one event, not two unpairable groups."""
    (event,) = load(
        tmp_path,
        {
            "1.csv": [HEADER_2026, close_row(), open_row()],
            "2.csv": [HEADER_2026, close_row(), open_row()],
        },
    )
    assert isinstance(event, StockSplitTransaction)
