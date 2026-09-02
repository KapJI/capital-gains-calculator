"""Dump parsed broker transactions in chronological order."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

    from .model import BrokerTransaction

__all__ = ["dump_transactions"]


def dump_transactions(
    transactions: Sequence[BrokerTransaction],
    stream: TextIO | None = None,
) -> None:
    """Print parsed broker transactions in chronological order.

    Args:
        transactions: Sequence of broker transactions to dump.
        stream: Output stream to write to (defaults to stdout).

    """
    if stream is None:
        stream = sys.stdout

    if not transactions:
        print("No transactions loaded.", file=sys.stderr)
        return

    header = (
        f"{'Date':10} | {'Action':22} | {'Symbol':6} | {'Qty':>12} | "
        f"{'Price':>12} | {'Amount':>16} | {'Fees':>8} | {'Broker':15}"
    )
    print(file=stream)
    print(header, file=stream)
    print("-" * len(header), file=stream)

    for t in transactions:
        qty_str = f"{t.quantity}" if t.quantity is not None else "-"
        price_str = (
            f"{t.price:.4f}".rstrip("0").rstrip(".") or "0"
            if t.price is not None
            else "-"
        )
        amount_str = f"{t.amount:.2f} {t.currency}" if t.amount is not None else "-"
        fees_str = f"{t.fees:.2f}"
        broker_str = t.broker or "-"

        row = (
            f"{t.date!s:10} | {t.action.name:22} | {t.symbol or '-':6} | "
            f"{qty_str:>12} | {price_str:>12} | {amount_str:>16} | "
            f"{fees_str:>8} | {broker_str:15}"
        )
        print(row, file=stream)

    print("-" * len(header), file=stream)
    print(f"Total: {len(transactions)} transaction(s)\n", file=stream)
