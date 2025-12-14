"""Test Schwab Cancel Buy filtering logic.

Tests the filtering of Cancel Buy transactions and their matching with original
Buy transactions within the search window.

Cancel Buy is a Schwab-specific transaction type that indicates a purchase was
cancelled. Both the original Buy and the Cancel Buy are mapped to ActionType.BUY,
and both need to be filtered out to avoid incorrect capital gains calculations.
"""

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.parsers.schwab import read_schwab_transactions


class TestCancelBuyFiltering:
    """Test Cancel Buy filtering logic."""

    def test_cancel_buy_removes_both_transactions(self, tmp_path: Path) -> None:
        """Test that Cancel Buy removes both the cancel and original Buy."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Both transactions should be removed
        assert len(transactions) == 0

    def test_cancel_buy_only_matches_within_5_days(self, tmp_path: Path) -> None:
        """Test that Cancel Buy only matches within 5-day window."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/01/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/10/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # 9 days apart - Cancel Buy won't match, both should remain
        assert len(transactions) == 2
        assert transactions[0].action.name == "BUY"  # Cancel Buy mapped to BUY
        assert transactions[1].action.name == "BUY"

    def test_cancel_buy_matches_exact_symbol_quantity_price(
        self, tmp_path: Path
    ) -> None:
        """Test that Cancel Buy matches exact symbol, quantity, and price."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$151.00,10,$0.00,-$1510.00\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Only the first Buy should be removed (exact price match)
        assert len(transactions) == 1
        assert transactions[0].price == Decimal("151.00")

    def test_cancel_buy_with_different_symbol_no_match(self, tmp_path: Path) -> None:
        """Test that Cancel Buy doesn't match different symbol."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/12/2024,Cancel Buy,MSFT,MICROSOFT CORP,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Different symbols - no match, both remain
        assert len(transactions) == 2

    def test_cancel_buy_with_fractional_shares(self, tmp_path: Path) -> None:
        """Test that Cancel Buy works with fractional shares."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10.5,$0.00,-$1575.00\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10.5,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Both should be removed (exact fractional quantity match)
        assert len(transactions) == 0

    def test_multiple_cancel_buys_same_symbol(self, tmp_path: Path) -> None:
        """Test multiple Cancel Buy transactions for the same symbol."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/11/2024,Buy,AAPL,APPLE INC,$151.00,20,$0.00,-$3020.00\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            "01/13/2024,Cancel Buy,AAPL,APPLE INC,$151.00,20,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # All should be removed
        assert len(transactions) == 0

    def test_cancel_buy_with_other_transactions(self, tmp_path: Path) -> None:
        """Test Cancel Buy doesn't affect unrelated transactions."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/09/2024,Buy,MSFT,MICROSOFT CORP,$200.00,5,$0.00,-$1000.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/11/2024,Sell,MSFT,MICROSOFT CORP,$205.00,5,$0.00,$1025.00\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Only AAPL Buy and Cancel Buy removed, MSFT transactions remain
        assert len(transactions) == 2
        assert all(txn.symbol == "MSFT" for txn in transactions)

    @pytest.mark.parametrize(
        "days_apart",
        [0, 1, 2, 3, 4, 5],
    )
    def test_cancel_buy_5_day_boundary(self, tmp_path: Path, days_apart: int) -> None:
        """Test that Cancel Buy matches within 5-day boundary."""
        buy_date = datetime.date(2024, 1, 10)
        cancel_date = buy_date + datetime.timedelta(days=days_apart)

        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            f"Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            f"{buy_date.strftime('%m/%d/%Y')},Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            f"{cancel_date.strftime('%m/%d/%Y')},Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Within 5 days - should match and remove both
        assert len(transactions) == 0

    def test_cancel_buy_6_days_no_match(self, tmp_path: Path) -> None:
        """Test that Cancel Buy doesn't match beyond 5-day window."""
        buy_date = datetime.date(2024, 1, 10)
        cancel_date = buy_date + datetime.timedelta(days=6)

        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            f"Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            f"{buy_date.strftime('%m/%d/%Y')},Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            f"{cancel_date.strftime('%m/%d/%Y')},Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # 6 days apart - no match, both remain
        assert len(transactions) == 2

    def test_no_cancel_buy_transactions(self, tmp_path: Path) -> None:
        """Test that normal transactions are unaffected when no Cancel Buy present."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/15/2024,Sell,AAPL,APPLE INC,$155.00,10,$0.00,$1550.00\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # No Cancel Buy - both transactions remain
        assert len(transactions) == 2
        assert transactions[0].action.name == "SELL"
        assert transactions[1].action.name == "BUY"
