"""Validators used for parsing arguments."""

from __future__ import annotations

import argparse
import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .const import INTERNAL_START_DATE

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

LOGGER = logging.getLogger(__name__)


def year_type(value: str) -> int:
    """Validate and convert year argument."""
    try:
        year = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from err

    min_year = INTERNAL_START_DATE.year
    max_year = datetime.datetime.now().year

    if year < min_year or year > max_year:
        raise argparse.ArgumentTypeError(
            f"year must be between {min_year} and {max_year}, got {year}"
        )

    return year


def ticker_list_type(value: str) -> list[str]:
    """Split comma-separated tickers and convert to uppercase list."""
    return [ticker.strip().upper() for ticker in value.split(",") if ticker.strip()]


def output_path_type(value: str) -> Path:
    """Validate non-empty output path and convert to Path."""
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    return Path(value)


def _ensure_readable_file(path: Path, value: str) -> None:
    """Raise ArgumentTypeError when file cannot be read."""
    try:
        with path.open("rb"):
            pass
    except OSError as err:
        raise argparse.ArgumentTypeError(
            f"unable to read file path: '{value}': {err}"
        ) from err


def _ensure_readable_directory(path: Path, value: str) -> None:
    """Raise ArgumentTypeError when directory contents cannot be listed."""
    try:
        iterator = path.iterdir()
        next(iterator, None)
    except OSError as err:
        raise argparse.ArgumentTypeError(
            f"unable to read directory path: '{value}': {err}"
        ) from err


def optional_file_type(value: str) -> Path | None:
    """Convert non-empty value to Path and ensure file semantics."""
    if value.strip() == "":
        return None
    path = Path(value)
    if path.exists():
        if not path.is_file():
            raise argparse.ArgumentTypeError(
                f"expected file path, got directory: '{value}'"
            )
        _ensure_readable_file(path, value)
    return path


def _existing_path_type(value: str, *, require_dir: bool) -> Path:
    """Ensure provided path exists and matches expected type."""
    path = Path(value).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: '{value}'")
    if require_dir and not path.is_dir():
        raise argparse.ArgumentTypeError(f"expected directory path, got: '{value}'")
    if not require_dir and not path.is_file():
        raise argparse.ArgumentTypeError(f"expected file path, got: '{value}'")
    if require_dir:
        _ensure_readable_directory(path, value)
    else:
        _ensure_readable_file(path, value)
    return path


def existing_file_type(value: str) -> Path:
    """Validate that provided value points to an existing file."""
    return _existing_path_type(value, require_dir=False)


def existing_directory_type(value: str) -> Path:
    """Validate that provided value points to an existing directory."""
    return _existing_path_type(value, require_dir=True)


class DeprecatedAction(argparse.Action):
    """Print warning when deprecated argument is used."""

    def __call__(  # type: ignore[explicit-any]
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        """Check if argument is deprecated."""
        assert isinstance(option_string, str), "Positional arguments are not supported"
        replacements: dict[str, str] = {
            "--freetrade": "--freetrade-file",
            "--initial-prices": "--initial-prices-file",
            "--mssb": "--mssb-dir",
            "--raw": "--raw-file",
            "--report": "--output",
            "--schwab": "--schwab-file",
            "--schwab-award": "--schwab-award-file",
            "--schwab_equity_award_json": "--schwab-equity-award-json",
            "--sharesight": "--sharesight-dir",
            "--trading212": "--trading212-dir",
            "--vanguard": "--vanguard-file",
        }
        LOGGER.warning(
            "Option '%s' is deprecated; use '%s' instead.",
            option_string,
            replacements[option_string],
        )
        setattr(namespace, self.dest, values)
