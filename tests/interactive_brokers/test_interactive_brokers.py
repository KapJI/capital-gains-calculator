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

    def test_basic_csv_file(self) -> None:
        """Runs the script and verifies it doesn't fail."""
        cmd = build_cmd(
            "--year",
            "2025",
            "--interactive-brokers-file",
            "tests/interactive_brokers/data/test_basic.csv",
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
