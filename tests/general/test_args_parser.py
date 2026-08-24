"""Tests for argument parser."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
import datetime
import logging
from pathlib import Path
from typing import IO, TextIO, cast

import pytest

from cgt_calc.args_parser import (
    create_parser,
    get_last_elapsed_tax_year,
    resolve_reporting_period,
)
from cgt_calc.args_validators import (
    STDIN_PATH,
    existing_directory_type,
    existing_file_type,
    optional_file_type,
)
from cgt_calc.const import (
    DEFAULT_EXCHANGE_RATES_FILE,
    DEFAULT_ISIN_TRANSLATION_FILE,
    DEFAULT_REPORT_PATH,
    DEFAULT_SPIN_OFF_FILE,
    INTERNAL_START_DATE,
)

ReturnType = TextIO | IO[bytes]
DirIterator = Iterator[Path]


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "tcsh", "powershell"])
def test_print_completion_outputs_script(
    shell: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test that --print-completion prints a completion script and exits."""
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--print-completion", shell])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--trading212-dir" in output
    assert "--initial-prices-file" in output


def test_print_completion_zsh_completes_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test path completers and hidden deprecated flags in the zsh script."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--print-completion", "zsh"])

    output = capsys.readouterr().out
    # Directory options complete directories, file options complete files.
    assert ":DIR:_files -/" in output
    assert ":PATH:_files" in output
    # Deprecated aliases are hidden from help and must not be completed.
    assert '"--report[' not in output
    assert '"--trading212[' not in output


def test_print_completion_zsh_covers_all_path_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that every PATH/DIR option in the zsh script completes paths."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--print-completion", "zsh"])

    output = capsys.readouterr().out
    missing = [
        line.strip()
        for line in output.splitlines()
        if (":PATH:" in line or ":DIR:" in line) and "_files" not in line
    ]
    assert not missing, f"options without a path completer: {missing}"


def test_version_prints_version_and_exits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Test that --version prints the version to stdout and exits."""
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("cgt-calc ")
    assert captured.out.endswith("\n")


def test_version_is_resolved_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that building the parser does not resolve the version."""

    def explode() -> str:
        raise AssertionError("version resolved without --version")

    monkeypatch.setattr("cgt_calc.args_validators.get_version", explode)

    parser = create_parser()
    args = parser.parse_args([])

    # --version is also kept out of the parsed arguments.
    assert not hasattr(args, "version")


def test_output_and_no_report_mutually_exclusive() -> None:
    """Test that --output and --no-report are mutually exclusive."""
    parser = create_parser()

    # Test that using both options raises SystemExit
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--output", "test.pdf", "--no-report"])

    # argparse exits with code 2 for argument errors
    assert exc_info.value.code == 2


def test_output_relative_path() -> None:
    """Test that --output accepts relative paths."""
    parser = create_parser()
    args = parser.parse_args(["--output", "reports/out.pdf"])

    assert args.output == Path("reports/out.pdf")
    assert args.no_report is False


def test_output_short_relative_path() -> None:
    """Test that -o accepts relative paths."""
    parser = create_parser()
    args = parser.parse_args(["-o", "reports/out.pdf"])

    assert args.output == Path("reports/out.pdf")
    assert args.no_report is False


def test_output_absolute_path(tmp_path: Path) -> None:
    """Test that --output accepts absolute paths."""
    absolute_path = tmp_path / "report.pdf"
    parser = create_parser()
    args = parser.parse_args(["--output", str(absolute_path)])

    assert args.output == absolute_path
    assert args.no_report is False


def test_output_rejects_empty_value() -> None:
    """Test that --output rejects empty string values."""
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--output", ""])

    assert exc_info.value.code == 2


def test_output_rejects_whitespace_value() -> None:
    """Test that --output rejects whitespace-only values."""
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--output", "   "])

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("option", "attr", "filename"),
    [
        ("--freetrade-file", "freetrade_file", "freetrade.csv"),
        ("--raw-file", "raw_file", "raw.csv"),
        ("--schwab-file", "schwab_file", "schwab.csv"),
        ("--schwab-award-file", "schwab_award_file", "schwab_award.csv"),
        (
            "--schwab-equity-award-json",
            "schwab_equity_award_json",
            "schwab_equity_award.json",
        ),
        ("--vanguard-file", "vanguard_file", "vanguard.csv"),
    ],
)
def test_broker_file_arguments_accept_existing_path(
    tmp_path: Path, option: str, attr: str, filename: str
) -> None:
    """Ensure broker file options accept existing files and return Path."""
    file_path = tmp_path / filename
    file_path.write_text("", encoding="utf-8")
    parser = create_parser()

    args = parser.parse_args([option, str(file_path)])

    assert getattr(args, attr) == file_path


@pytest.mark.parametrize(
    ("option", "attr"),
    [
        ("--freetrade-file", "freetrade_file"),
        ("--raw-file", "raw_file"),
        ("--schwab-file", "schwab_file"),
        ("--schwab-award-file", "schwab_award_file"),
        ("--schwab-equity-award-json", "schwab_equity_award_json"),
        ("--vanguard-file", "vanguard_file"),
    ],
)
def test_broker_file_arguments_reject_missing_path(
    tmp_path: Path, option: str, attr: str
) -> None:
    """Ensure broker file options reject missing paths."""
    parser = create_parser()
    missing_path = tmp_path / "does_not_exist.csv"

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([option, str(missing_path)])

    assert exc_info.value.code == 2
    args = parser.parse_args([])
    assert getattr(args, attr) is None


@pytest.mark.parametrize(
    ("option", "attr", "dirname"),
    [
        ("--hl-dir", "hl_dir", "hl"),
        ("--mssb-dir", "mssb_dir", "mssb"),
        ("--sharesight-dir", "sharesight_dir", "sharesight"),
        ("--trading212-dir", "trading212_dir", "trading212"),
    ],
)
def test_broker_dir_arguments_accept_existing_directory(
    tmp_path: Path, option: str, attr: str, dirname: str
) -> None:
    """Ensure broker directory options accept existing directories."""
    dir_path = tmp_path / dirname
    dir_path.mkdir()
    parser = create_parser()

    args = parser.parse_args([option, str(dir_path)])

    assert getattr(args, attr) == dir_path


@pytest.mark.parametrize(
    ("option", "attr"),
    [
        ("--mssb-dir", "mssb_dir"),
        ("--sharesight-dir", "sharesight_dir"),
        ("--trading212-dir", "trading212_dir"),
    ],
)
def test_broker_dir_arguments_reject_invalid_paths(
    tmp_path: Path, option: str, attr: str
) -> None:
    """Ensure broker directory options reject missing directories or files."""
    parser = create_parser()
    missing_dir = tmp_path / "missing"

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([option, str(missing_dir)])

    assert exc_info.value.code == 2

    file_path = tmp_path / "not_a_dir.csv"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([option, str(file_path)])

    assert exc_info.value.code == 2

    args = parser.parse_args([])
    assert getattr(args, attr) is None


def test_existing_directory_type_rejects_unreadable_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """existing_directory_type raises when directory listing fails."""
    directory = tmp_path / "blocked"
    directory.mkdir()
    target = directory
    original_iterdir = cast(
        "Callable[[Path], DirIterator]",
        Path.iterdir,
    )

    def fake_iterdir(self: Path) -> DirIterator:
        if self == target:
            raise PermissionError("Permission denied")
        return original_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    with pytest.raises(
        argparse.ArgumentTypeError, match="unable to read directory path"
    ):
        existing_directory_type(str(directory))


@pytest.mark.parametrize(
    ("option", "attr", "default"),
    [
        ("--exchange-rates-file", "exchange_rates_file", DEFAULT_EXCHANGE_RATES_FILE),
        (
            "--isin-translation-file",
            "isin_translation_file",
            DEFAULT_ISIN_TRANSLATION_FILE,
        ),
        ("--spin-offs-file", "spin_offs_file", DEFAULT_SPIN_OFF_FILE),
    ],
)
def test_optional_path_arguments_default(option: str, attr: str, default: Path) -> None:
    """Ensure optional path arguments use their default when not provided."""
    parser = create_parser()
    args = parser.parse_args([])

    assert getattr(args, attr) == default


@pytest.mark.parametrize(
    ("option", "attr", "value"),
    [
        ("--exchange-rates-file", "exchange_rates_file", "custom_rates.csv"),
        ("--isin-translation-file", "isin_translation_file", "custom_isin.csv"),
        ("--spin-offs-file", "spin_offs_file", "custom_spin_offs.csv"),
    ],
)
def test_optional_path_arguments_accept_custom_path(
    option: str, attr: str, value: str
) -> None:
    """Ensure optional path arguments convert provided values to Path."""
    parser = create_parser()
    args = parser.parse_args([option, value])

    assert getattr(args, attr) == Path(value)


@pytest.mark.parametrize(
    ("option", "attr"),
    [
        ("--exchange-rates-file", "exchange_rates_file"),
        ("--isin-translation-file", "isin_translation_file"),
        ("--spin-offs-file", "spin_offs_file"),
    ],
)
def test_optional_path_arguments_allow_empty_string(option: str, attr: str) -> None:
    """Ensure optional path arguments treat empty values as None."""
    parser = create_parser()
    args = parser.parse_args([option, ""])

    assert getattr(args, attr) is None


@pytest.mark.parametrize(
    ("option", "attr"),
    [
        ("--exchange-rates-file", "exchange_rates_file"),
        ("--isin-translation-file", "isin_translation_file"),
        ("--spin-offs-file", "spin_offs_file"),
    ],
)
def test_optional_path_arguments_reject_directory(
    tmp_path: Path, option: str, attr: str
) -> None:
    """Ensure optional path arguments reject directories when they exist."""
    parser = create_parser()
    directory = tmp_path / "existing_dir"
    directory.mkdir()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([option, str(directory)])

    assert exc_info.value.code == 2
    args = parser.parse_args([])
    assert getattr(args, attr) is not None


def test_optional_file_type_rejects_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """optional_file_type raises when file cannot be read."""
    target = tmp_path / "data.csv"
    target.write_text("value,1\n", encoding="utf8")
    original_open = cast(
        "Callable[[Path, str, int, str | None, str | None, str | None], ReturnType]",
        Path.open,
    )

    def fake_open(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> ReturnType:
        if self == target:
            raise PermissionError("Permission denied")
        return original_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fake_open)

    with pytest.raises(argparse.ArgumentTypeError, match="unable to read file path"):
        optional_file_type(str(target))


def test_existing_file_type_rejects_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """existing_file_type raises when file cannot be read."""
    target = tmp_path / "data.csv"
    target.write_text("value,1\n", encoding="utf8")
    original_open = cast(
        "Callable[[Path, str, int, str | None, str | None, str | None], ReturnType]",
        Path.open,
    )

    def fake_open(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> ReturnType:
        if self == target:
            raise PermissionError("Permission denied")
        return original_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", fake_open)

    with pytest.raises(argparse.ArgumentTypeError, match="unable to read file path"):
        existing_file_type(str(target))


def test_no_report_alone_works() -> None:
    """Test that --no-report works alone."""
    parser = create_parser()
    args = parser.parse_args(["--no-report"])

    assert args.no_report is True
    # output still has default value
    assert args.output == DEFAULT_REPORT_PATH


def test_short_option_output_and_no_report_mutually_exclusive() -> None:
    """Test that -o and --no-report are mutually exclusive."""
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["-o", "test.pdf", "--no-report"])

    assert exc_info.value.code == 2


def test_default_output_when_neither_specified() -> None:
    """Test default output path when neither option is specified."""
    parser = create_parser()
    args = parser.parse_args([])

    assert args.output == DEFAULT_REPORT_PATH
    assert args.no_report is False


def test_year_validation_too_early() -> None:
    """Test that year before INTERNAL_START_DATE is rejected."""
    parser = create_parser()
    min_year = INTERNAL_START_DATE.year
    invalid_year = min_year - 1

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--year", str(invalid_year)])

    assert exc_info.value.code == 2


def test_year_validation_too_late() -> None:
    """Test that year in the future is rejected."""
    parser = create_parser()
    current_year = datetime.datetime.now().year
    invalid_year = current_year + 1

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--year", str(invalid_year)])

    assert exc_info.value.code == 2


def test_year_validation_min_valid() -> None:
    """Test that minimum valid year is accepted."""
    parser = create_parser()
    min_year = INTERNAL_START_DATE.year

    args = parser.parse_args(["--year", str(min_year)])

    assert args.year == min_year


def test_year_validation_max_valid() -> None:
    """Test that maximum valid year (current year) is accepted."""
    parser = create_parser()
    current_year = datetime.datetime.now().year

    args = parser.parse_args(["--year", str(current_year)])

    assert args.year == current_year


def test_year_validation_valid_middle() -> None:
    """Test that a year in the middle of valid range is accepted."""
    parser = create_parser()
    min_year = INTERNAL_START_DATE.year
    current_year = datetime.datetime.now().year
    middle_year = (min_year + current_year) // 2

    args = parser.parse_args(["--year", str(middle_year)])

    assert args.year == middle_year


def test_interest_fund_tickers_single() -> None:
    """Test that a single ticker is parsed correctly."""
    parser = create_parser()

    args = parser.parse_args(["--interest-fund-tickers", "VGOV"])

    assert args.interest_fund_tickers == ["VGOV"]


def test_interest_fund_tickers_multiple() -> None:
    """Test that multiple tickers are parsed correctly."""
    parser = create_parser()

    args = parser.parse_args(["--interest-fund-tickers", "VGOV,VBMFX,VWEHX"])

    assert args.interest_fund_tickers == ["VGOV", "VBMFX", "VWEHX"]


def test_interest_fund_tickers_with_spaces() -> None:
    """Test that tickers with spaces are trimmed correctly."""
    parser = create_parser()

    args = parser.parse_args(["--interest-fund-tickers", " VGOV , VBMFX , VWEHX "])

    assert args.interest_fund_tickers == ["VGOV", "VBMFX", "VWEHX"]


def test_interest_fund_tickers_lowercase() -> None:
    """Test that lowercase tickers are converted to uppercase."""
    parser = create_parser()

    args = parser.parse_args(["--interest-fund-tickers", "vgov,vbmfx"])

    assert args.interest_fund_tickers == ["VGOV", "VBMFX"]


def test_interest_fund_tickers_empty_default() -> None:
    """Test that default is an empty list when not specified."""
    parser = create_parser()

    args = parser.parse_args([])

    assert args.interest_fund_tickers == []


def test_interest_fund_tickers_empty_items_filtered() -> None:
    """Test that empty items (e.g., trailing commas) are filtered out."""
    parser = create_parser()

    args = parser.parse_args(["--interest-fund-tickers", "VGOV,,VBMFX,"])

    assert args.interest_fund_tickers == ["VGOV", "VBMFX"]


def test_existing_file_type_stdin() -> None:
    """Ensure existing_file_type returns STDIN_PATH when passed '-'."""
    assert existing_file_type("-") == STDIN_PATH


def test_raw_file_stdin() -> None:
    """Ensure --raw-file - correctly returns STDIN_PATH."""
    parser = create_parser()
    args = parser.parse_args(["--raw-file", "-"])
    assert args.raw_file == STDIN_PATH


def test_initial_prices_alias_warns_deprecated(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The hidden --initial-prices alias still works but warns."""
    file_path = tmp_path / "prices.csv"
    file_path.write_text("", encoding="utf-8")
    parser = create_parser()

    with caplog.at_level(logging.WARNING):
        args = parser.parse_args(["--initial-prices", str(file_path)])

    assert args.initial_prices_file == file_path
    assert "Option '--initial-prices' is deprecated" in caplog.text
    assert "--initial-prices-file" in caplog.text


def test_resolve_period_from_to() -> None:
    """Test that --from/--to resolve the tax year from the range."""
    parser = create_parser()
    args = parser.parse_args(["--from", "2024-04-06", "--to", "2024-10-29"])

    resolve_reporting_period(parser, args)

    assert args.year == 2024
    assert args.period_from == datetime.date(2024, 4, 6)
    assert args.period_to == datetime.date(2024, 10, 29)


def test_resolve_period_defaults_year_when_absent() -> None:
    """Test that --year defaults to the last elapsed tax year."""
    parser = create_parser()
    args = parser.parse_args([])

    resolve_reporting_period(parser, args)

    assert args.year == get_last_elapsed_tax_year()


@pytest.mark.parametrize(
    "argv",
    [
        # --from and --to must be used together.
        ["--from", "2024-04-06"],
        ["--to", "2024-10-29"],
        # --year cannot be combined with a custom period.
        ["--year", "2024", "--from", "2024-04-06", "--to", "2024-10-29"],
        # --from must not be after --to.
        ["--from", "2024-10-29", "--to", "2024-04-06"],
        # The period must stay within one UK tax year.
        ["--from", "2024-04-05", "--to", "2024-04-06"],
        ["--from", "2024-10-29", "--to", "2025-04-06"],
    ],
)
def test_resolve_period_rejects_invalid_combinations(argv: list[str]) -> None:
    """Test that invalid --year/--from/--to combinations exit with an error."""
    parser = create_parser()
    args = parser.parse_args(argv)

    with pytest.raises(SystemExit) as exc_info:
        resolve_reporting_period(parser, args)

    assert exc_info.value.code == 2


def test_from_rejects_invalid_date() -> None:
    """Test that malformed dates are rejected at parse time."""
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--from", "2024-13-01", "--to", "2024-12-31"])

    assert exc_info.value.code == 2
