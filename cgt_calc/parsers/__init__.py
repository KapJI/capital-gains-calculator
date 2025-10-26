"""Parse input files."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .eri import read_eri_transactions
from .freetrade import read_freetrade_transactions
from .mssb import read_mssb_transactions
from .raw import read_raw_transactions
from .schwab import read_schwab_transactions
from .schwab_equity_award_json import read_schwab_equity_award_json_transactions
from .sharesight import read_sharesight_transactions
from .trading212 import read_trading212_transactions
from .vanguard import read_vanguard_transactions

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction

LOGGER = logging.getLogger(__name__)


def read_broker_transactions(
    *,
    freetrade_transactions_file: Path | None,
    schwab_transactions_file: Path | None,
    schwab_awards_transactions_file: Path | None,
    schwab_equity_award_json_transactions_file: Path | None,
    trading212_transactions_folder: Path | None,
    mssb_transactions_folder: Path | None,
    sharesight_transactions_folder: Path | None,
    raw_transactions_file: Path | None,
    vanguard_transactions_file: Path | None,
    eri_raw_file: Path | None,
) -> list[BrokerTransaction]:
    """Read transactions for all brokers."""
    transactions = []

    if schwab_transactions_file is not None:
        transactions += read_schwab_transactions(
            schwab_transactions_file, schwab_awards_transactions_file
        )
    else:
        LOGGER.debug("No Schwab file provided")

    if schwab_equity_award_json_transactions_file is not None:
        transactions += read_schwab_equity_award_json_transactions(
            schwab_equity_award_json_transactions_file
        )
    else:
        LOGGER.debug("No Schwab Equity Award JSON file provided")

    if trading212_transactions_folder is not None:
        transactions += read_trading212_transactions(trading212_transactions_folder)
    else:
        LOGGER.debug("No Trading212 folder provided")

    if mssb_transactions_folder is not None:
        transactions += read_mssb_transactions(mssb_transactions_folder)
    else:
        LOGGER.debug("No MSSB folder provided")

    if sharesight_transactions_folder is not None:
        transactions += read_sharesight_transactions(sharesight_transactions_folder)
    else:
        LOGGER.debug("No Sharesight file provided")

    if raw_transactions_file is not None:
        transactions += read_raw_transactions(raw_transactions_file)
    else:
        LOGGER.debug("No RAW file provided")

    if vanguard_transactions_file is not None:
        transactions += read_vanguard_transactions(vanguard_transactions_file)
    else:
        LOGGER.debug("No Vanguard file provided")

    if freetrade_transactions_file is not None:
        transactions += read_freetrade_transactions(freetrade_transactions_file)
    else:
        LOGGER.debug("No Freetrade file provided")

    if len(transactions) == 0:
        LOGGER.warning("Found 0 broker transactions")
    else:
        print(f"Found {len(transactions)} broker transactions")

    transactions += read_eri_transactions(eri_raw_file)

    transactions.sort(key=lambda k: k.date)
    return transactions
