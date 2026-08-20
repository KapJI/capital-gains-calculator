"""Test Interactive Brokers parser."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.interactive_brokers import InteractiveBrokersParser
from tests.utils import build_cmd, stderr_alerts


class TestInteractiveBrokers:
    """Test Interactive Brokers parser."""

    base_header = (
        """
Statement,Header,Field Name,Field Value
Statement,Data,Title,Transaction History
Statement,Data,Period,"January 1, 2024 - December 31, 2025"
Statement,Data,WhenGenerated,"2025-12-31, 23:59:59 EST"
Summary,Header,Field Name,Field Value
Summary,Data,Base Currency,GBP
Summary,Data,Starting Cash,0.0
Summary,Data,Change,200.00
Summary,Data,Ending Cash,200.00
Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Gross Amount ,Commission,Net Amount
""".strip()
        + "\n"
    )

    base_header_with_foreign_currency = (
        """
Statement,Header,Field Name,Field Value
Statement,Data,Title,Transaction History
Statement,Data,Period,"January 1, 2024 - December 31, 2025"
Statement,Data,WhenGenerated,"2025-12-31, 23:59:59 EST"
Summary,Header,Field Name,Field Value
Summary,Data,Base Currency,GBP
Summary,Data,Starting Cash,0.0
Summary,Data,Change,200.00
Summary,Data,Ending Cash,200.00
Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount,Exchange Rate
""".strip()
        + "\n"
    )

    def test_single_buy(self, tmp_path: Path) -> None:
        """Test parsing a single buy transaction."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-10-13,U***08040,ISHARES NASDAQ 100 USD ACC,Buy,CNX1,1.0,1060.3,-1060.3,-3.0,-1063.3\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        txn = transactions[0]
        expected = BrokerTransaction(
            date=date(2025, 10, 13),
            action=ActionType.BUY,
            symbol="CNX1",
            description="ISHARES NASDAQ 100 USD ACC",
            quantity=Decimal("1.0"),
            price=Decimal("1060.3"),
            fees=Decimal("3.0"),
            amount=Decimal("-1063.3"),
            currency="GBP",
            broker="Interactive Brokers",
            isin=None,
        )

        assert txn.date == expected.date
        assert txn.action == expected.action
        assert txn.symbol == expected.symbol
        assert txn.description == expected.description
        assert txn.quantity == expected.quantity
        assert txn.price == expected.price
        assert txn.fees == expected.fees
        assert txn.amount == expected.amount
        assert txn.currency == expected.currency
        assert txn.broker == expected.broker

    def test_isin_is_read_from_the_description(self, tmp_path: Path) -> None:
        """Dividend rows name the security as TICKER(ISIN); trades do not."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-10-01,U***08040,"
            "VT(US9220427424) Cash Dividend USD 0.3272 per Share (Ordinary Dividend),"
            "Payment in Lieu,VT,-,-,1.0,-,1.0\n"
            + "Transaction History,Data,2025-10-13,U***08040,"
            "ISHARES NASDAQ 100 USD ACC,Buy,CNX1,1.0,1060.3,-1060.3,-3.0,-1063.3\n"
        )

        dividend, trade = InteractiveBrokersParser().load_from_file(csv_file)

        assert dividend.isin == "US9220427424"
        assert trade.isin is None

    def test_invalid_isin_in_the_description_is_ignored(self, tmp_path: Path) -> None:
        """A malformed code must not be passed off as an ISIN."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-10-01,U***08040,"
            "VT(US9220427429) Cash Dividend USD 0.3272 per Share (Ordinary Dividend),"
            "Payment in Lieu,VT,-,-,1.0,-,1.0\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert transactions[0].isin is None

    def test_buy_foreign_currency_price(self, tmp_path: Path) -> None:
        """Test that a buy with a foreign-currency price is converted to GBP correctly.

        In IBKR exports the Gross/Net Amount and Commission are always in the account's
        base currency (GBP), while Price can be in the instrument's trading currency.
        The parser must convert Price to GBP using the Exchange Rate so that the
        internal consistency check (quantity x price + fees ≈ |amount|) passes.

        Transaction: Buy 217 IWDE at EUR 67.71, exchange rate 0.88542 EUR→GBP.
        GBP gross = 217 x 67.71 x 0.88542 = 13009.5380394
        GBP commission = 6.5047690197
        GBP net = 13016.0428084197
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header_with_foreign_currency
            + "Transaction History,Data,2023-02-10,U***9143,ISHARES MSCI WORLD EUR-H,Buy,IWDE,217,67.71,EUR,-13009.5380394,-6.5047690197,-13016.0428084197,0.88542\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        txn = transactions[0]
        expected_price_gbp = Decimal("67.71") * Decimal("0.88542")
        assert txn.date == date(2023, 2, 10)
        assert txn.action == ActionType.BUY
        assert txn.symbol == "IWDE"
        assert txn.quantity == Decimal(217)
        assert txn.price == expected_price_gbp
        assert txn.fees == Decimal("6.5047690197")
        assert txn.amount == Decimal("-13016.0428084197")
        assert txn.currency == "GBP"

    def test_basic_csv_file(self) -> None:
        """Runs the script and verifies it doesn't fail."""
        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            "tests/interactive_brokers/data/test_basic.csv",
            "--output",
            "out/test-interactive_brokers/",
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
        expected_file = (
            Path("tests") / "interactive_brokers" / "data" / "expected_output.txt"
        )
        expected = expected_file.read_text()
        cmd_str = " ".join([param or "''" for param in cmd])
        assert result.stdout == expected, (
            "Run with example files generated unexpected outputs, "
            "if you added new features update the test with:\n"
            f"{cmd_str} > {expected_file}"
        )

    def test_ordinary_dividend_and_its_withholding(self, tmp_path: Path) -> None:
        """An ordinary dividend is income, and the US tax on it is withheld.

        The parser knew "Payment in Lieu", which arrives instead of a dividend
        when the holding is out on loan, but not the ordinary dividend that
        every holder of a paying security receives. Anyone with one hit
        "Unknown type: 'Dividend'" on their first real statement.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-11-03,U***00000,VT(US9220427424) Cash Dividend USD 0.50 per Share,Dividend,VT,-,-,20.0,-,20.0\n"
            + "Transaction History,Data,2025-11-03,U***00000,VT(US9220427424) Cash Dividend - US Tax,Foreign Tax Withholding,VT,-,-,-3.0,-,-3.0\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert [t.action for t in transactions] == [
            ActionType.DIVIDEND,
            ActionType.DIVIDEND_TAX,
        ]
        assert transactions[0].amount == Decimal("20.0")
        assert transactions[0].symbol == "VT"
        assert transactions[1].amount == Decimal("-3.0")

    def test_fx_translation_adjusts_the_balance_only(self, tmp_path: Path) -> None:
        """Revaluing a currency balance is not a disposal.

        IBKR reports "FX Translations P&L" as an Adjustment. It moves the cash
        balance and creates no taxable event: exchange movement on a personal
        currency balance is outside CGT. The Schwab parser treats its own
        Adjustment the same way.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-11-30,U***00000,FX Translations P&L,Adjustment,-,-,-,0.55,-,0.55\n"
        )

        transactions = InteractiveBrokersParser().load_from_file(csv_file)

        assert len(transactions) == 1
        assert transactions[0].action == ActionType.ADJUSTMENT
        assert transactions[0].amount == Decimal("0.55")
        # No quantity and no price, so it cannot become an acquisition or a
        # disposal however it is grouped downstream.
        assert transactions[0].quantity is None
        assert transactions[0].price is None

    def test_buy_before_same_day_sell_does_not_go_negative(
        self, tmp_path: Path
    ) -> None:
        """A same-day buy listed before the sell funding it must not go negative.

        The file lists the buy of AAA before the sell of BBB on the same day,
        even though the cash to pay for the buy only exists once the sell is
        accounted for. If the transactions were processed in file order the
        cash balance would briefly go negative and trip the balance check.
        Sorting sells before buys on the same day (post_process_transactions)
        avoids that, and the amounts are chosen so the buy exactly consumes
        the sell's proceeds, leaving a final cash balance of zero.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header
            + "Transaction History,Data,2025-01-01,U***00000,Electronic Fund Transfer,Deposit,-,-,-,1000.0,-,1000.0\n"
            + "Transaction History,Data,2025-01-01,U***00000,BBB STOCK,Buy,BBB,10.0,100.0,-1000.0,-,-1000.0\n"
            # Same day, buy listed before the sell that funds it.
            + "Transaction History,Data,2025-06-02,U***00000,AAA STOCK,Buy,AAA,10.0,100.0,-1000.0,-,-1000.0\n"
            + "Transaction History,Data,2025-06-02,U***00000,BBB STOCK,Sell,BBB,-10.0,100.0,1000.0,-,1000.0\n"
        )

        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            str(csv_file),
            "--output",
            str(tmp_path / "out"),
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
        assert "Final balance\n  Interactive Brokers: 0.00 (GBP)" in result.stdout

    def test_withholding_before_its_dividend_does_not_go_negative(
        self, tmp_path: Path
    ) -> None:
        """IBKR lists tax withheld at source before the dividend it came from.

        A statement that does not reach back to the opening of the account
        starts at a zero balance, so in file order the withholding is the first
        row and takes the balance negative before the dividend that covers it
        arrives on the same day.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            self.base_header + "Transaction History,Data,2025-06-24,U***00000,"
            "VT(US9220427424) Cash Dividend - US Tax,Foreign Tax Withholding,"
            "VT,-,-,-15.0,-,-15.0\n" + "Transaction History,Data,2025-06-24,U***00000,"
            "VT(US9220427424) Cash Dividend USD 0.5 per Share,Dividend,"
            "VT,-,-,100.0,-,100.0\n"
        )

        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            str(csv_file),
            "--output",
            str(tmp_path / "out"),
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert stderr_alerts(result.stderr) == []
