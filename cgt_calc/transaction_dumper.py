"""Transaction dumper module for formatting and exporting broker transactions."""

from __future__ import annotations

import csv
import dataclasses
from enum import Enum
import io
from pathlib import Path
import sys
from typing import TYPE_CHECKING, TextIO

import tabulate

from .model import BrokerTransaction

if TYPE_CHECKING:
    from collections.abc import Sequence

# Mapping from tablefmt names to commonly recognized file extensions
TABLEFMT_EXTENSIONS: dict[str, str] = {
    "csv": "csv",
    "tsv": "tsv",
    "html": "html",
    "unsafehtml": "html",
    "latex": "tex",
    "latex_raw": "tex",
    "latex_booktabs": "tex",
    "latex_longtable": "tex",
    "github": "md",
    "pipe": "md",
    "rst": "rst",
    "asciidoc": "adoc",
    "mediawiki": "wiki",
    "moinmoin": "moin",
    "orgtbl": "org",
    "textile": "textile",
}


def get_dump_extension(tablefmt: str) -> str:
    """Return the file extension corresponding to the given table format.

    Args:
        tablefmt: The tabulate table format name or 'csv'.

    Returns:
        The file extension string without leading dot (e.g. 'tsv', 'csv', 'txt').

    """
    return TABLEFMT_EXTENSIONS.get(tablefmt.lower(), "txt")


def resolve_columns(
    all_columns: Sequence[str],
    columns: Sequence[str] | None = None,
    exclude_columns: Sequence[str] | None = None,
) -> list[str]:
    """Resolve and filter column names according to inclusion and exclusion lists.

    Args:
        all_columns: Sequence of all available column names.
        columns: Optional sequence of column names to include.
        exclude_columns: Optional sequence of column names to exclude.

    Returns:
        Filtered list of column names.

    Raises:
        ValueError: If any column in columns or exclude_columns does not exist in all_columns.

    """
    col_map = {c.lower(): c for c in all_columns}

    if columns is not None:
        invalid_cols = [c for c in columns if c.strip().lower() not in col_map]
        if invalid_cols:
            invalid_str = ", ".join(f"'{c.strip()}'" for c in invalid_cols)
            valid_str = ", ".join(all_columns)
            raise ValueError(
                f"unknown column(s) for BrokerTransaction: {invalid_str}. "
                f"Valid columns are: {valid_str}"
            )
        selected: list[str] = []
        for col in columns:
            canonical = col_map[col.strip().lower()]
            if canonical not in selected:
                selected.append(canonical)
    else:
        selected = list(all_columns)

    if exclude_columns is not None:
        invalid_cols = [c for c in exclude_columns if c.strip().lower() not in col_map]
        if invalid_cols:
            invalid_str = ", ".join(f"'{c.strip()}'" for c in invalid_cols)
            valid_str = ", ".join(all_columns)
            raise ValueError(
                f"unknown column(s) for BrokerTransaction: {invalid_str}. "
                f"Valid columns are: {valid_str}"
            )
        exclude_set = {col.strip().lower() for col in exclude_columns}
        selected = [c for c in selected if c.lower() not in exclude_set]

    return selected


def format_transactions(
    transactions: Sequence[BrokerTransaction],
    tablefmt: str = "simple",
    columns: Sequence[str] | None = None,
    exclude_columns: Sequence[str] | None = None,
    maxcolwidths: int | Sequence[int | None] | None = None,
) -> str:
    """Format broker transactions as a tabular string or CSV.

    Converts each BrokerTransaction into a dictionary and formats the resulting
    list with tabulate or standard library csv.

    Args:
        transactions: Sequence of BrokerTransaction instances to format.
        tablefmt: The output table format ('csv' or any tabulate tablefmt).
        columns: Optional sequence of column names to include.
        exclude_columns: Optional sequence of column names to exclude.
        maxcolwidths: Maximum column width(s) passed to tabulate.

    Returns:
        The formatted string representing all transactions.

    """
    all_fieldnames = [field.name for field in dataclasses.fields(BrokerTransaction)]
    selected_cols = resolve_columns(
        all_fieldnames,
        columns=columns,
        exclude_columns=exclude_columns,
    )

    rows: list[dict[str, object]] = []
    for t in transactions:
        row = dataclasses.asdict(t)
        if hasattr(t.action, "name"):
            row["action"] = t.action.name
        for k, v in row.items():
            if isinstance(v, Enum):
                row[k] = v.name
        filtered_row = {col: row.get(col, "") for col in selected_cols}
        rows.append(filtered_row)

    if tablefmt.lower() == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=selected_cols)
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()

    if not rows:
        return tabulate.tabulate(
            [],
            headers=selected_cols,
            tablefmt=tablefmt,
            maxcolwidths=maxcolwidths,
        )

    return tabulate.tabulate(
        rows,
        headers="keys",
        tablefmt=tablefmt,
        maxcolwidths=maxcolwidths,
    )


def dump_transactions(
    transactions: Sequence[BrokerTransaction],
    tablefmt: str = "simple",
    output_file: str | Path | None = None,
    stream: TextIO | None = None,
    columns: Sequence[str] | None = None,
    exclude_columns: Sequence[str] | None = None,
    maxcolwidths: int | Sequence[int | None] | None = None,
) -> Path | None:
    """Dump broker transactions to stdout or a file.

    Args:
        transactions: Sequence of BrokerTransaction instances to dump.
        tablefmt: Format name (e.g. 'simple', 'tsv', 'csv', 'html').
        output_file: Destination path, '-' for stdout, or None for default.
        stream: Optional stream to write to (overrides output_file if specified).
        columns: Optional sequence of column names to include.
        exclude_columns: Optional sequence of column names to exclude.
        maxcolwidths: Maximum column width(s) passed to tabulate.

    Returns:
        Path of the output file written, or None if written to stdout/stream.

    """
    content = format_transactions(
        transactions,
        tablefmt=tablefmt,
        columns=columns,
        exclude_columns=exclude_columns,
        maxcolwidths=maxcolwidths,
    )

    if stream is not None:
        stream.write(content)
        if not content.endswith("\n"):
            stream.write("\n")
        return None

    if output_file == "-":
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
        return None

    ext = get_dump_extension(tablefmt)
    dest_path = (
        Path(output_file)
        if output_file is not None
        else Path(f"parsed_transaction.{ext}")
    )

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with dest_path.open("w", encoding="utf-8", newline="") as f:
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")

    return dest_path
