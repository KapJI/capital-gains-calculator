"""Test Vanguard parser support."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.vanguard import COLUMNS, VanguardParser
from tests.utils import build_cmd


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")


def test_run_with_vanguard_files() -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2022",
        "--vanguard-file",
        "tests/vanguard/data/cash_investment_report.csv",
        "--interest-fund-tickers",
        "FOO",
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert result.stderr == "", "Run with example files generated errors"
    expected_file = Path("tests") / "vanguard" / "data" / "expected_output.txt"
    expected = expected_file.read_text()
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_read_vanguard_transactions_buy(tmp_path: Path) -> None:
    """Parse a simple BUY transaction and compute derived fields."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund (FOO)",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "FOO"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal(10)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_missing_symbol(tmp_path: Path) -> None:
    """Use the full string investment name if no symbol within ( ) is matched."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "Foo Fund"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal(10)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_fractional_share(tmp_path: Path) -> None:
    """Make sure it handles fractional share."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought .2 Foo Fund (GBP) (FOO)",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "FOO"
    assert transaction.quantity is not None
    assert transaction.quantity - Decimal("0.2") < Decimal("0.000001")
    assert transaction.price == Decimal(500)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when amount cannot be parsed as Decimal."""

    vanguard_file = tmp_path / "invalid.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund (FOO)",
            "not-a-number",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    with pytest.raises(ParsingError) as exc:
        VanguardParser().load_from_file(vanguard_file)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal" in message


def test_read_vanguard_transactions_invalid_header(tmp_path: Path) -> None:
    """Raise ParsingError when header contains unexpected column."""

    vanguard_file = tmp_path / "invalid_header.csv"
    rows = [
        [
            "Date",
            "Details",
            "Unexpected",
            "Balance",
        ],
    ]
    _write_csv(vanguard_file, rows)

    with pytest.raises(ParsingError) as exc:
        VanguardParser().load_from_file(vanguard_file)

    assert "Expected column 3 to be 'Amount' but found 'Unexpected'" in str(exc.value)


def test_read_vanguard_transactions_empty_file(tmp_path: Path) -> None:
    """Raise ParsingError when file has no content."""

    vanguard_file = tmp_path / "empty.csv"
    vanguard_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        VanguardParser().load_from_file(vanguard_file)

    assert "Vanguard CSV file is empty" in str(exc.value)


# --- Tests for investment transaction enrichment ---

INVESTMENT_DATA_DIR = Path("tests/vanguard/data")


def test_dual_table_enriches_buy_with_investment_data() -> None:
    """Cash BUY transactions are enriched with precise quantity/price from investment table."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    foo_buys = [
        t for t in transactions if t.symbol == "FOO" and t.action == ActionType.BUY
    ]
    assert len(foo_buys) == 1
    assert foo_buys[0].quantity == Decimal(1550)
    assert foo_buys[0].price == Decimal("14982.06") / Decimal(1550)


def test_dual_table_enriches_sell_with_investment_data() -> None:
    """Cash SELL transactions are enriched with precise price from investment table."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    bar_sells = [
        t for t in transactions if t.symbol == "BAR" and t.action == ActionType.SELL
    ]
    assert len(bar_sells) == 1
    assert bar_sells[0].quantity == Decimal(1)
    assert bar_sells[0].price == Decimal("104.06")


def test_dual_table_enriches_amount_from_investment_cost() -> None:
    """Enriched amount equals quantity * price from investment table."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    bar_buys = [
        t for t in transactions if t.symbol == "BAR" and t.action == ActionType.BUY
    ]
    assert len(bar_buys) == 1
    # amount stays as the cash table value
    assert bar_buys[0].amount == Decimal("-29947.28")


def test_dual_table_fractional_shares_from_investment() -> None:
    """Fractional shares from investment table are used over cash regex parsing."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    em_buys = [
        t
        for t in transactions
        if t.symbol == "Emerging Markets Stock Index Fund - Accumulation"
        and t.action == ActionType.BUY
    ]
    assert len(em_buys) == 1
    assert em_buys[0].quantity == Decimal("6.7700")
    assert em_buys[0].price == Decimal(1000) / Decimal("6.7700")


def test_dual_table_preserves_non_investment_transactions() -> None:
    """Transfers, interest, and dividends from cash table are preserved."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    transfers = [t for t in transactions if t.action == ActionType.TRANSFER]
    assert len(transfers) > 0

    interests = [t for t in transactions if t.action == ActionType.INTEREST]
    assert len(interests) == 1
    assert interests[0].amount == Decimal("0.41")

    dividends = [t for t in transactions if t.action == ActionType.DIVIDEND]
    assert len(dividends) == 4


def test_dual_table_reversal_handling() -> None:
    """Reversal transactions are correctly flagged."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    reversals = [t for t in transactions if hasattr(t, "is_reversal") and t.is_reversal]
    assert len(reversals) == 1
    assert reversals[0].action == ActionType.DIVIDEND
    assert reversals[0].amount == Decimal("-170.83")


def test_dual_table_sorted_by_date() -> None:
    """Transactions are sorted by date."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    dates = [t.date for t in transactions]
    assert dates == sorted(dates)


def test_investment_only_table(tmp_path: Path) -> None:
    """File with only an Investment Transactions table is parsed correctly."""
    content = (
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "10/03/2022,Foo ETF (FOO),Bought 10 Foo ETF (FOO),10,9.5,95\n"
        "15/03/2022,Foo ETF (FOO),Sold 5 Foo ETF (FOO),5,10.0,50\n"
    )
    vanguard_file = tmp_path / "inv_only.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 2
    assert transactions[0].action == ActionType.BUY
    assert transactions[0].quantity == Decimal(10)
    assert transactions[0].price == Decimal("9.5")
    assert transactions[1].action == ActionType.SELL


def test_namechange_emits_rename_transaction(tmp_path: Path) -> None:
    """Parser emits a RENAME transaction; pre-rename buys keep the old ticker.

    Symbol unification happens later in the calculator preprocessor. Leaving
    the pre-rename buys under OLD at the parser layer keeps the rename event
    visible end-to-end for audit.
    """
    content = (
        "Cash Transactions\n"
        "\n"
        "Date,Details,Amount,Balance\n"
        "22/06/2020,Bought 100 DAX UCITS ETF Distributing (VDXX),-1000.00,0\n"
        "28/09/2020,"
        "Bought 50 Vanguard Germany All Cap UCITS ETF (EUR) Distributing (VGER),"
        "-600.00,0\n"
        "\n"
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "18/09/2020,Vanguard Germany All Cap UCITS ETF (EUR) Distributing (VGER),"
        "NameChange: VDXX.XLON.GB replaced with VGER.XLON.GB,"
        "100,11.00,1100.00\n"
        "18/09/2020,DAX UCITS ETF Distributing (VDXX),NameChange: VDXX.XLON.GB,"
        "-100,11.00,-1100.00\n"
    )
    vanguard_file = tmp_path / "namechange.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 3

    renames = [t for t in transactions if t.action is ActionType.RENAME]
    assert len(renames) == 1
    rename = renames[0]
    assert rename.symbol == "VGER"
    assert rename.date.isoformat() == "2020-09-18"
    assert "VDXX" in rename.description
    assert rename.amount == Decimal(0)

    # Pre-rename buy of VDXX remains under VDXX at the parser layer.
    buys = [t for t in transactions if t.action is ActionType.BUY]
    assert len(buys) == 2
    buy_by_date = {t.date.isoformat(): t for t in buys}
    assert buy_by_date["2020-06-22"].symbol == "VDXX"
    assert buy_by_date["2020-09-28"].symbol == "VGER"


def test_namechange_in_investment_only_table_emits_rename(tmp_path: Path) -> None:
    """NameChange rows in an investment-only CSV emit a RENAME transaction."""
    content = (
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "18/09/2020,Vanguard Germany All Cap UCITS ETF (EUR) Distributing (VGER),"
        "NameChange: VDXX.XLON.GB replaced with VGER.XLON.GB,"
        "100,11.00,1100.00\n"
        "18/09/2020,DAX UCITS ETF Distributing (VDXX),NameChange: VDXX.XLON.GB,"
        "-100,11.00,-1100.00\n"
        "25/09/2020,Vanguard Germany All Cap UCITS ETF (EUR) Distributing (VGER),"
        "Bought 50 Vanguard Germany All Cap UCITS ETF (EUR) Distributing (VGER),"
        "50,12.00,600.00\n"
    )
    vanguard_file = tmp_path / "inv_namechange.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 2

    renames = [t for t in transactions if t.action is ActionType.RENAME]
    assert len(renames) == 1
    assert renames[0].symbol == "VGER"
    assert "VDXX" in renames[0].description

    buys = [t for t in transactions if t.action is ActionType.BUY]
    assert len(buys) == 1
    assert buys[0].symbol == "VGER"
    assert buys[0].quantity == Decimal(50)
