"""Test Schwab corporate action transaction pairing.

Tests the pairing of corporate action transactions (Cash Merger and Full Redemption)
where split transaction rows need to be combined into unified transactions with
calculated prices.
"""

from decimal import Decimal
from pathlib import Path

from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import read_schwab_transactions


class TestCorporateActionPairing:
    """Test Cash Merger and Full Redemption transaction pairing."""

    def test_cash_merger_pairing(self, tmp_path: Path) -> None:
        """Test Cash Merger + Cash Merger Adj are correctly combined."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "03/02/2021,Cash Merger,FOO,FOO INC,,,,$1000\n"
            "03/02/2021,Cash Merger Adj,FOO,FOO INC,,-100,$5,\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should have 1 unified transaction
        assert len(transactions) == 1
        unified = transactions[0]

        # Verify unified transaction
        assert unified.action == ActionType.CASH_MERGER
        assert unified.symbol == "FOO"
        assert unified.quantity == Decimal(100)  # Converted to positive
        assert unified.amount == Decimal(1000)
        assert unified.price == Decimal(10)  # 1000 / 100
        assert unified.fees == Decimal(5)  # From Adj transaction

    def test_full_redemption_pairing(self, tmp_path: Path) -> None:
        """Test Full Redemption Adj + Full Redemption are correctly combined."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "05/15/2023,Full Redemption Adj,BAR,BAR CORP,,,,$2500\n"
            "05/15/2023,Full Redemption,BAR,BAR CORP,,-50,,\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should have 1 unified transaction
        assert len(transactions) == 1
        unified = transactions[0]

        # Verify unified transaction
        assert unified.action == ActionType.FULL_REDEMPTION
        assert unified.symbol == "BAR"
        assert unified.quantity == Decimal(50)  # Converted to positive
        assert unified.amount == Decimal(2500)
        assert unified.price == Decimal(50)  # 2500 / 50
        assert unified.fees == Decimal(0)  # No fees

    def test_cash_merger_with_other_transactions(self, tmp_path: Path) -> None:
        """Test Cash Merger pairing doesn't affect other transactions."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/01/2021,Buy,AAPL,APPLE INC,$150,10,$1,-$1501\n"
            "03/02/2021,Cash Merger,FOO,FOO INC,,,,$1000\n"
            "03/02/2021,Cash Merger Adj,FOO,FOO INC,,-100,,\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should have 2 transactions: unified Cash Merger + Buy
        # Note: transactions are reversed, so most recent first
        assert len(transactions) == 2
        assert transactions[0].action == ActionType.CASH_MERGER
        assert transactions[0].quantity == Decimal(100)
        assert transactions[1].action == ActionType.BUY
        assert transactions[1].symbol == "AAPL"

    def test_multiple_cash_mergers(self, tmp_path: Path) -> None:
        """Test multiple Cash Merger pairs are handled correctly."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "03/02/2021,Cash Merger,FOO,FOO INC,,,,$1000\n"
            "03/02/2021,Cash Merger Adj,FOO,FOO INC,,-100,,\n"
            "04/15/2021,Cash Merger,BAR,BAR CORP,,,,$5000\n"
            "04/15/2021,Cash Merger Adj,BAR,BAR CORP,,-200,,\n"
        )

        transactions = read_schwab_transactions(csv_file, None)

        # Should have 2 unified transactions
        # Note: transactions are reversed, so most recent first
        assert len(transactions) == 2

        # Second merger: BAR (most recent, first in reversed list)
        assert transactions[0].symbol == "BAR"
        assert transactions[0].quantity == Decimal(200)
        assert transactions[0].price == Decimal(25)

        # First merger: FOO
        assert transactions[1].symbol == "FOO"
        assert transactions[1].quantity == Decimal(100)
        assert transactions[1].price == Decimal(10)
