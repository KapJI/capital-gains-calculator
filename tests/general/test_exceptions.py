"""Unit tests for custom exception helpers."""

from __future__ import annotations

from pathlib import Path

from cgt_calc.exceptions import ParsingError


def test_parsing_error_message_without_row() -> None:
    """Base message includes file and original detail."""

    err = ParsingError(Path("file.csv"), "broken header")

    assert str(err) == "While parsing file.csv: broken header"


def test_parsing_error_message_with_row() -> None:
    """Row context is included when supplied at construction."""

    err = ParsingError(Path("file.csv"), "bad value", row_index=7)

    assert str(err) == "While parsing file.csv, row 7: bad value"


def test_parsing_error_add_row_context_updates_message() -> None:
    """Adding row context after creation updates the message."""

    err = ParsingError(Path("file.csv"), "bad value")

    err.add_row_context(9)
    assert str(err) == "While parsing file.csv, row 9: bad value"

    err.add_row_context(11)
    assert str(err) == "While parsing file.csv, row 11: bad value"
