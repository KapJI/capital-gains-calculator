"""Unit and integration tests for transaction dumping functionality."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
import io
from pathlib import Path
import subprocess

import pytest

from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
from cgt_calc.transaction_dumper import (
    dump_transactions,
    format_transactions,
    get_dump_extension,
    resolve_columns,
)
from tests.utils import build_cmd, report_path


def _sample_transactions() -> list[BrokerTransaction]:
    """Generate sample transactions for testing."""
    return [
        BrokerTransaction(
            date=datetime.date(2023, 4, 25),
            action=ActionType.STOCK_ACTIVITY,
            symbol="GOOG",
            description="RS Vest",
            quantity=Decimal("10.5"),
            price=Decimal("100.25"),
            fees=Decimal("1.50"),
            amount=Decimal("1054.125"),
            currency=CurrencyCode("USD"),
            broker="Test Broker",
        ),
        BrokerTransaction(
            date=datetime.date(2023, 5, 1),
            action=ActionType.DIVIDEND,
            symbol=None,
            description="Cash Dividend",
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("50.00"),
            currency=CurrencyCode("GBP"),
            broker="",
        ),
    ]


@pytest.mark.parametrize(
    ("tablefmt", "expected_ext"),
    [
        ("csv", "csv"),
        ("tsv", "tsv"),
        ("html", "html"),
        ("unsafehtml", "html"),
        ("latex", "tex"),
        ("latex_raw", "tex"),
        ("latex_booktabs", "tex"),
        ("latex_longtable", "tex"),
        ("github", "md"),
        ("pipe", "md"),
        ("rst", "rst"),
        ("asciidoc", "adoc"),
        ("mediawiki", "wiki"),
        ("simple", "txt"),
        ("grid", "txt"),
        ("plain", "txt"),
        ("pretty", "txt"),
        ("psql", "txt"),
    ],
)
def test_get_dump_extension(tablefmt: str, expected_ext: str) -> None:
    """Verify file extension resolution for various table formats."""
    assert get_dump_extension(tablefmt) == expected_ext
    # Check case-insensitivity
    assert get_dump_extension(tablefmt.upper()) == expected_ext


def test_format_transactions_csv() -> None:
    """CSV output generates valid comma-separated values with headers."""
    txs = _sample_transactions()
    output = format_transactions(txs, tablefmt="csv")

    reader = list(csv.reader(io.StringIO(output)))
    assert len(reader) == 3  # header + 2 rows
    assert "date" in reader[0]
    assert "action" in reader[0]
    assert "symbol" in reader[0]
    assert "quantity" in reader[0]

    # Check Row 1
    date_idx = reader[0].index("date")
    action_idx = reader[0].index("action")
    symbol_idx = reader[0].index("symbol")
    amount_idx = reader[0].index("amount")
    assert reader[1][date_idx] == "2023-04-25"
    assert reader[1][action_idx] == "STOCK_ACTIVITY"
    assert "ActionType." not in output
    assert reader[1][symbol_idx] == "GOOG"
    assert reader[1][amount_idx] == "1054.125"

    # Check Row 2 (None fields are empty strings in CSV)
    assert reader[2][date_idx] == "2023-05-01"
    assert reader[2][action_idx] == "DIVIDEND"
    assert reader[2][symbol_idx] == ""


def test_format_transactions_action_excludes_classname() -> None:
    """Action field values do not include ActionType class prefix in dumped output."""
    txs = _sample_transactions()
    output = format_transactions(txs, tablefmt="simple")
    assert "ActionType" not in output
    assert "STOCK_ACTIVITY" in output
    assert "DIVIDEND" in output


def test_format_transactions_columns_filter() -> None:
    """Only requested columns are included in output, in specified order."""
    txs = _sample_transactions()
    output = format_transactions(
        txs, tablefmt="csv", columns=["symbol", "amount", "date"]
    )
    reader = list(csv.reader(io.StringIO(output)))

    assert reader[0] == ["symbol", "amount", "date"]
    assert reader[1] == ["GOOG", "1054.125", "2023-04-25"]


def test_format_transactions_exclude_columns() -> None:
    """Excluded columns are omitted from output."""
    txs = _sample_transactions()
    output = format_transactions(
        txs, tablefmt="csv", exclude_columns=["source", "foreign_fees", "action"]
    )
    reader = list(csv.reader(io.StringIO(output)))

    assert "source" not in reader[0]
    assert "foreign_fees" not in reader[0]
    assert "action" not in reader[0]
    assert "date" in reader[0]
    assert "symbol" in reader[0]


def test_format_transactions_columns_and_exclude_columns() -> None:
    """When both columns and exclude_columns are specified, exclusions are removed."""
    txs = _sample_transactions()
    output = format_transactions(
        txs,
        tablefmt="csv",
        columns=["date", "action", "symbol", "amount"],
        exclude_columns=["action"],
    )
    reader = list(csv.reader(io.StringIO(output)))

    assert reader[0] == ["date", "symbol", "amount"]


def test_resolve_columns_rejects_unknown_columns() -> None:
    """resolve_columns raises ValueError when unknown column is passed in columns."""
    all_cols = ["date", "action", "symbol"]
    with pytest.raises(ValueError, match=r"unknown column.*invalid_col"):
        resolve_columns(all_cols, columns=["date", "invalid_col"])


def test_resolve_columns_rejects_unknown_exclude_columns() -> None:
    """resolve_columns raises ValueError when unknown column is passed in exclude_columns."""
    all_cols = ["date", "action", "symbol"]
    with pytest.raises(ValueError, match=r"unknown column.*invalid_col"):
        resolve_columns(all_cols, exclude_columns=["invalid_col"])


def test_format_transactions_rejects_unknown_columns() -> None:
    """format_transactions raises ValueError when unknown column is passed."""
    txs = _sample_transactions()
    with pytest.raises(ValueError, match=r"unknown column.*non_existent_column"):
        format_transactions(txs, columns=["non_existent_column"])


def test_format_transactions_rejects_unknown_exclude_columns() -> None:
    """format_transactions raises ValueError when unknown column is passed in exclude_columns."""
    txs = _sample_transactions()
    with pytest.raises(ValueError, match=r"unknown column.*non_existent_column"):
        format_transactions(txs, exclude_columns=["non_existent_column"])


def test_format_transactions_maxcolwidths_int() -> None:
    """Passing an integer maxcolwidths wraps long text in table output."""
    txs = _sample_transactions()
    output = format_transactions(
        txs,
        tablefmt="simple",
        columns=["date", "description"],
        maxcolwidths=5,
    )
    # The description 'RS Vest' should be wrapped
    lines = output.splitlines()
    assert "date" in lines[0]
    assert "descr" in lines[0]


def test_format_transactions_maxcolwidths_list() -> None:
    """Passing a sequence maxcolwidths sets width per column in table output."""
    txs = _sample_transactions()
    output = format_transactions(
        txs,
        tablefmt="simple",
        columns=["date", "description", "amount"],
        maxcolwidths=[10, 5, 10],
    )
    assert "date" in output


def test_format_transactions_empty_csv() -> None:
    """Empty transaction list still emits CSV headers."""
    output = format_transactions([], tablefmt="csv")
    reader = list(csv.reader(io.StringIO(output)))
    assert len(reader) == 1
    assert "date" in reader[0]
    assert "symbol" in reader[0]


def test_format_transactions_tsv() -> None:
    """TSV output uses tab characters to separate columns."""
    txs = _sample_transactions()
    output = format_transactions(txs, tablefmt="tsv")
    lines = output.splitlines()

    assert len(lines) == 3  # header + 2 rows
    assert "\t" in lines[0]
    assert "GOOG" in lines[1]


def test_format_transactions_html() -> None:
    """HTML table format produces table markup."""
    txs = _sample_transactions()
    output = format_transactions(txs, tablefmt="html")

    assert "<table>" in output
    assert "date" in output
    assert "<td>2023-04-25</td>" in output
    assert "GOOG" in output


def test_format_transactions_simple() -> None:
    """Simple format produces text table with header underlines."""
    txs = _sample_transactions()
    output = format_transactions(txs, tablefmt="simple")
    lines = output.splitlines()

    assert len(lines) == 4  # header + underline + 2 rows
    assert "date" in lines[0]
    assert "---" in lines[1]
    assert "GOOG" in lines[2]


def test_dump_transactions_to_stream() -> None:
    """dump_transactions writes to a custom stream when provided."""
    txs = _sample_transactions()
    buf = io.StringIO()
    result = dump_transactions(txs, tablefmt="csv", stream=buf)

    assert result is None
    assert "date,action,symbol" in buf.getvalue()


def test_dump_transactions_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """dump_transactions writes to stdout when output_file is '-'."""
    txs = _sample_transactions()
    result = dump_transactions(txs, tablefmt="tsv", output_file="-")

    assert result is None
    captured = capsys.readouterr()
    assert "\t" in captured.out
    assert "GOOG" in captured.out


def test_dump_transactions_to_file(tmp_path: Path) -> None:
    """dump_transactions writes to specified file path."""
    txs = _sample_transactions()
    out_file = tmp_path / "subdir" / "dump.csv"
    result = dump_transactions(txs, tablefmt="csv", output_file=out_file)

    assert result == out_file
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "date,action,symbol" in content
    assert "GOOG" in content


def test_dump_transactions_default_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dump_transactions defaults to parsed_transaction.{ext} in current directory."""
    monkeypatch.chdir(tmp_path)
    txs = _sample_transactions()
    result = dump_transactions(txs, tablefmt="html")

    expected_path = Path("parsed_transaction.html")
    assert result == expected_path
    assert expected_path.exists()
    assert "<table>" in expected_path.read_text(encoding="utf-8")


def test_cli_dump_transactions_default_file(
    request: pytest.FixtureRequest,
) -> None:
    """CLI creates parsed_transaction.txt by default and completes calculation."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    dump_file = Path("parsed_transaction.txt")
    try:
        assert result.returncode == 0, (
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
        assert dump_file.exists()
        content = dump_file.read_text(encoding="utf-8")
        assert "GOOG" in content
        assert "Tax summary for 2023/2024" in result.stdout
    finally:
        dump_file.unlink(missing_ok=True)


def test_cli_dump_transactions_csv_format(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    """CLI creates parsed_transaction.csv when format is csv."""
    dump_file = tmp_path / "custom.csv"
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "csv",
        "--dump-transactions-output",
        str(dump_file),
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert dump_file.exists()
    content = dump_file.read_text(encoding="utf-8")
    assert "date,action,symbol" in content
    assert "GOOG" in content


def test_cli_dump_transactions_to_stdout(
    request: pytest.FixtureRequest,
) -> None:
    """CLI writes table to stdout when --dump-transactions-output is '-'."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "tsv",
        "--dump-transactions-output",
        "-",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "date" in result.stdout
    assert "\t" in result.stdout
    assert "GOOG" in result.stdout
    assert "Tax summary for 2023/2024" in result.stdout


def test_cli_dump_transactions_columns_filter(
    request: pytest.FixtureRequest,
) -> None:
    """CLI --dump-transactions-columns limits columns in dumped output."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "csv",
        "--dump-transactions-columns",
        "date,action,amount",
        "--dump-transactions-output",
        "-",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "date,action,amount" in result.stdout
    assert "symbol" not in result.stdout
    assert "ActionType" not in result.stdout
    assert "STOCK_ACTIVITY" in result.stdout


def test_cli_dump_transactions_exclude_columns(
    request: pytest.FixtureRequest,
) -> None:
    """CLI --dump-transactions-exclude-columns omits columns in dumped output."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        "csv",
        "--dump-transactions-exclude-columns",
        "source,foreign_fees,calculation_quantity,option_contract,written_option_tax,capital_adjustments",
        "--dump-transactions-output",
        "-",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "source" not in result.stdout
    assert "foreign_fees" not in result.stdout
    assert "date" in result.stdout
    assert "symbol" in result.stdout
