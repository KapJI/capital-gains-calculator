"""Test Schwab 'as of' date parsing functionality.

Tests the parsing of Schwab's "MM/DD/YYYY as of MM/DD/YYYY" date format,
where the first date is the settlement date (used as primary transaction date)
and the second date (after "as of") is the vest date (stored separately for
vest-date same-day matching).
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest

from cgt_calc.parsers.schwab import read_schwab_transactions

if TYPE_CHECKING:
    from pathlib import Path


class TestAsOfDateParsing:
    """Test 'as of' date parsing functionality."""

    def test_normal_date_without_as_of(self, tmp_path: Path) -> None:
        """Test that normal dates without 'as of' are parsed correctly."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/15/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 1
        assert transactions[0].date == datetime.date(2024, 1, 15)
        assert transactions[0].vest_date is None

    def test_date_with_as_of(self, tmp_path: Path) -> None:
        """Test that settlement date is used as primary, vest date stored separately."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "02/16/2024 as of 02/15/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should use settlement date (02/16/2024) as primary date
        # and vest date (02/15/2024) stored separately
        assert len(transactions) == 1
        assert transactions[0].date == datetime.date(2024, 2, 16)
        assert transactions[0].vest_date == datetime.date(2024, 2, 15)

    def test_stock_plan_activity_with_as_of(self, tmp_path: Path) -> None:
        """Test real-world example: Stock Plan Activity with 'as of' date."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "08/18/2023 as of 08/15/2023,Buy,BAR,BAR CORP INC CLASS A,$100.00,200,$0.00,-$20000.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should use settlement date 08/18/2023 as primary
        # and vest date 08/15/2023 stored separately
        assert len(transactions) == 1
        assert transactions[0].date == datetime.date(2023, 8, 18)
        assert transactions[0].vest_date == datetime.date(2023, 8, 15)

    def test_as_of_date_ordering(self, tmp_path: Path) -> None:
        """Test that transactions are ordered correctly using settlement dates."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/12/2024 as of 01/08/2024,Buy,AAPL,APPLE INC,$145.00,5,$0.00,-$725.00\n"
            "01/15/2024,Sell,AAPL,APPLE INC,$155.00,10,$0.00,$1550.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Transactions are reversed, so most recent first
        # Settlement date (before "as of") is now used for ordering
        assert len(transactions) == 3
        assert transactions[0].date == datetime.date(2024, 1, 15)  # Sell
        assert transactions[1].date == datetime.date(
            2024, 1, 12
        )  # Buy with "as of" (settlement)
        assert transactions[1].vest_date == datetime.date(2024, 1, 8)  # Vest date
        assert transactions[2].date == datetime.date(2024, 1, 10)  # Buy

    def test_as_of_with_different_months(self, tmp_path: Path) -> None:
        """Test 'as of' date that crosses month boundary."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "02/01/2024 as of 01/31/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should use settlement date 02/01/2024, not vest date 01/31/2024
        assert len(transactions) == 1
        assert transactions[0].date == datetime.date(2024, 2, 1)
        assert transactions[0].vest_date == datetime.date(2024, 1, 31)

    def test_as_of_with_different_years(self, tmp_path: Path) -> None:
        """Test 'as of' date that crosses year boundary."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/02/2024 as of 12/29/2023,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should use settlement date 01/02/2024, not vest date 12/29/2023
        assert len(transactions) == 1
        assert transactions[0].date == datetime.date(2024, 1, 2)
        assert transactions[0].vest_date == datetime.date(2023, 12, 29)

    @pytest.mark.parametrize(
        ("date_str", "expected_settlement", "expected_vest"),
        [
            (
                "01/15/2024 as of 01/10/2024",
                datetime.date(2024, 1, 15),
                datetime.date(2024, 1, 10),
            ),
            (
                "12/31/2023 as of 12/30/2023",
                datetime.date(2023, 12, 31),
                datetime.date(2023, 12, 30),
            ),
            (
                "02/29/2024 as of 02/28/2024",
                datetime.date(2024, 2, 29),
                datetime.date(2024, 2, 28),
            ),
            (
                "1/5/2024 as of 1/3/2024",
                datetime.date(2024, 1, 5),
                datetime.date(2024, 1, 3),
            ),
            (
                "01/05/2024 as of 01/03/2024",
                datetime.date(2024, 1, 5),
                datetime.date(2024, 1, 3),
            ),
        ],
    )
    def test_as_of_date_formats(
        self,
        tmp_path: Path,
        date_str: str,
        expected_settlement: datetime.date,
        expected_vest: datetime.date,
    ) -> None:
        """Test various 'as of' date format variations."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            f"Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            f"{date_str},Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        assert len(transactions) == 1
        assert transactions[0].date == expected_settlement
        assert transactions[0].vest_date == expected_vest
