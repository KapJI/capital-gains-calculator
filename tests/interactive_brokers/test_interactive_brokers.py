"""Test Interactive Brokers parser."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.interactive_brokers import InteractiveBrokersParser
from tests.utils import build_cmd


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
        """Test that bond buy price is divided by 100 for CUSIP symbols."""
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
            "--exchange-rates-file",
            "tests/exchange_rates_data.csv"
        )
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode:
            pytest.fail(
                "Integration test failed\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        assert result.stderr == ""
        expected_file = (
            Path("tests") / "interactive_brokers" / "data" / "expected_output.txt"
        )
        expected = expected_file.read_text()
        cmd_str = " ".join([param if param else "''" for param in cmd])
        assert result.stdout == expected, (
            "Run with example files generated unexpected outputs, "
            "if you added new features update the test with:\n"
            f"{cmd_str} > {expected_file}"
        )
