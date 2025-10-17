"""Tests for argument parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from cgt_calc.args_parser import create_parser


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
