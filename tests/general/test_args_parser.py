"""Tests for argument parser."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.const import INTERNAL_START_DATE


def test_output_and_no_report_mutually_exclusive() -> None:
    """Test that --output and --no-report are mutually exclusive."""
    parser = create_parser()

    # Test that using both options raises SystemExit
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--output", "test.pdf", "--no-report"])

    # argparse exits with code 2 for argument errors
    assert exc_info.value.code == 2


def test_output_alone_works() -> None:
    """Test that --output works alone."""
    parser = create_parser()
    args = parser.parse_args(["--output", "custom.pdf"])

    assert args.output == "custom.pdf"
    assert args.no_report is False


def test_no_report_alone_works() -> None:
    """Test that --no-report works alone."""
    parser = create_parser()
    args = parser.parse_args(["--no-report"])

    assert args.no_report is True
    # output still has default value
    assert args.output == Path("out/calculations.pdf")


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

    assert args.output == Path("out/calculations.pdf")
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
