"""Test Schwab parser."""

import datetime
from decimal import Decimal
import io
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError, SymbolMissingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser, action_from_str
from tests.utils import build_cmd, stderr_alerts


@pytest.fixture(autouse=True)
def _reset_awards_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep changes to the class-level award prices out of other test modules."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))


def test_missing_award_file_reports_the_vest_it_cannot_price() -> None:
    """A vest without a price is the only thing the award file is needed for.

    The runs below parse files that never need it and stay silent; this one
    reaches a Stock Plan Activity row and has nothing to price it with.
    """
    path = Path("tests") / "schwab" / "data" / "rsu_settlement" / "transactions.csv"
    SchwabParser.awards_prices = AwardPrices(award_prices={})

    with (
        path.open(encoding="utf-8") as csv_file,
        pytest.raises(ParsingError) as exc_info,
    ):
        SchwabParser.read_transactions(csv_file, path)

    message = str(exc_info.value)
    assert "no Schwab Award file was provided" in message
    assert "--schwab-award-file" in message
    # The row that needs the file is named, so it can be found in the statement.
    assert "BAR" in message
    assert "2023-08-18" in message


def test_award_file_without_the_award_says_so() -> None:
    """A file that lacks this particular award is a different mistake."""
    path = Path("tests") / "schwab" / "data" / "rsu_settlement" / "transactions.csv"
    SchwabParser.awards_prices = AwardPrices(
        award_prices={datetime.date(2020, 1, 1): {"FOO": Decimal(1)}}
    )

    with (
        path.open(encoding="utf-8") as csv_file,
        pytest.raises(ParsingError) as exc_info,
    ):
        SchwabParser.read_transactions(csv_file, path)

    assert "has no award price for it" in str(exc_info.value)


def test_run_with_schwab_example_2023_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/2023/transactions.csv",
        "--output",
        "out/test-schwab-2023/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = Path("tests") / "schwab" / "data" / "2023" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_cash_merger_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--schwab-file",
        "tests/schwab/data/cash_merger/transactions.csv",
        "--output",
        "out/test-schwab-cash-merger/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    alerts = stderr_alerts(result.stderr)
    assert len(alerts) == 1
    assert alerts[0].startswith("WARNING: Cash Merger support is not complete")
    assert "FOO: 100 units on 2021-03-02 for 1000 USD (Charles Schwab)" in result.stderr
    expected_file = (
        Path("tests") / "schwab" / "data" / "cash_merger" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_rsu_settlement_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/rsu_settlement/transactions.csv",
        "--schwab-award-file",
        "tests/schwab/data/rsu_settlement/awards.csv",
        "--output",
        "out/test-schwab-rsu_settlement/",
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
        Path("tests") / "schwab" / "data" / "rsu_settlement" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_bond_interest_files() -> None:
    """Test that Bond Interest and Credit Interest are classified as INTEREST."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/bond_interest/transactions.csv",
        "--output",
        "out/test-schwab-bond_interest/",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = (
        Path("tests") / "schwab" / "data" / "bond_interest" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_interest_tax_files() -> None:
    """Runs the script on interest withholding data and checks the output."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--schwab-file",
        "tests/schwab/data/interest_tax/transactions.csv",
        "--output",
        "out/test-schwab-interest-tax/",
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
        Path("tests") / "schwab" / "data" / "interest_tax" / "expected_output.txt"
    )
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


SCHWAB_HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"


def _read(content: str) -> list[BrokerTransaction]:
    """Parse a Schwab CSV from a string."""
    return SchwabParser.read_transactions(
        io.StringIO(content), Path("transactions.csv")
    )


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("Buy", ActionType.BUY),
        ("Sell", ActionType.SELL),
        ("Qualified Dividend", ActionType.DIVIDEND),
        ("Cash Dividend", ActionType.DIVIDEND),
        ("Qual Div Reinvest", ActionType.DIVIDEND),
        ("Div Adjustment", ActionType.DIVIDEND),
        ("Special Qual Div", ActionType.DIVIDEND),
        ("Non-Qualified Div", ActionType.DIVIDEND),
        ("NRA Tax Adj", ActionType.DIVIDEND_TAX),
        ("NRA Withholding", ActionType.DIVIDEND_TAX),
        ("Foreign Tax Paid", ActionType.DIVIDEND_TAX),
        ("ADR Mgmt Fee", ActionType.FEE),
        ("Adjustment", ActionType.ADJUSTMENT),
        ("IRS Withhold Adj", ActionType.ADJUSTMENT),
        ("Wire Funds Adj", ActionType.ADJUSTMENT),
        ("Short Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Long Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Spin-off", ActionType.SPIN_OFF),
        ("Credit Interest", ActionType.INTEREST),
        ("Bond Interest", ActionType.INTEREST),
        ("Reinvest Shares", ActionType.REINVEST_SHARES),
        ("Reinvest Dividend", ActionType.REINVEST_DIVIDENDS),
        ("Wire Funds Received", ActionType.WIRE_FUNDS_RECEIVED),
        ("Stock Split", ActionType.STOCK_SPLIT),
        ("Cash Merger", ActionType.CASH_MERGER),
        ("Cash Merger Adj", ActionType.CASH_MERGER),
        ("Full Redemption", ActionType.FULL_REDEMPTION),
        ("Full Redemption Adj", ActionType.FULL_REDEMPTION),
    ],
)
def test_action_from_str(label: str, action: ActionType) -> None:
    """Map every known Schwab action label."""
    assert action_from_str(label, Path("transactions.csv")) == action


def test_action_from_str_unknown() -> None:
    """Raise on unknown action labels."""
    with pytest.raises(ParsingError, match="Unknown action"):
        action_from_str("Dance", Path("transactions.csv"))


def test_read_transactions_empty_file() -> None:
    """Raise on empty transaction files."""
    with pytest.raises(ParsingError, match="file is empty"):
        _read("")


def test_read_transactions_missing_columns() -> None:
    """Raise when required columns are missing."""
    with pytest.raises(ParsingError, match="Missing columns"):
        _read("Date,Action\n01/15/2023,Sell\n")


def test_read_transactions_skips_blank_lines() -> None:
    """Skip blank lines in the file."""
    content = SCHWAB_HEADER + ",,,,,,,\n"
    assert _read(content) == []


def test_read_transactions_wrong_column_count() -> None:
    """Raise when a row has a different number of columns."""
    content = SCHWAB_HEADER + "01/15/2023,Sell\n"
    with pytest.raises(ParsingError) as excinfo:
        _read(content)
    assert excinfo.value.row_index == 2


def test_read_transactions_as_of_date() -> None:
    """Parse dates with an 'as of' suffix."""
    content = (
        SCHWAB_HEADER
        + '01/15/2023 as of 01/12/2023,Credit Interest,,Interest,,,,"$1.00"\n'
    )
    transactions = _read(content)
    assert transactions[0].date == datetime.date(2023, 1, 15)


def test_read_transactions_invalid_date() -> None:
    """Raise on unparsable dates."""
    content = SCHWAB_HEADER + "2023-01-15,Credit Interest,,Interest,,,,$1.00\n"
    with pytest.raises(ParsingError, match="Invalid date format"):
        _read(content)


def test_read_transactions_ninth_column_must_be_empty() -> None:
    """Raise when the legacy ninth column contains data."""
    header = SCHWAB_HEADER.rstrip("\n") + ",Extra\n"
    content = header + "01/15/2023,Credit Interest,,Interest,,,,$1.00,oops\n"
    with pytest.raises(ParsingError, match="should be empty"):
        _read(content)


def test_stock_activity_without_symbol() -> None:
    """Raise when a stock activity row has no symbol to price."""
    content = SCHWAB_HEADER + "01/15/2023,Stock Plan Activity,,Vest,,10,,\n"
    with pytest.raises(SymbolMissingError):
        _read(content)


def test_cash_merger_adj_without_cash_merger() -> None:
    """Raise when a Cash Merger Adj has no preceding Cash Merger."""
    content = SCHWAB_HEADER + "01/15/2023,Cash Merger Adj,FOO,Merger,,-10,,\n"
    with pytest.raises(ParsingError, match="must be preceded"):
        _read(content)


def test_invalid_cash_merger_pair() -> None:
    """Raise when a Cash Merger pair fails validation."""
    content = (
        SCHWAB_HEADER
        + '01/16/2023,Cash Merger,FOO,Merger,,,,"$100.00"\n'
        + '01/15/2023,Cash Merger Adj,FOO,Merger,,-10,,"$5.00"\n'
    )
    with pytest.raises(ParsingError, match="Invalid Cash Merger format"):
        _read(content)


def test_invalid_full_redemption_pair() -> None:
    """Raise when a Full Redemption pair fails validation."""
    content = (
        SCHWAB_HEADER
        + '01/16/2023,Full Redemption Adj,FOO,Redemption,,,,"$100.00"\n'
        + '01/15/2023,Full Redemption,FOO,Redemption,"$1.00",-10,,\n'
    )
    with pytest.raises(ParsingError, match="Invalid Full Redemption format"):
        _read(content)
