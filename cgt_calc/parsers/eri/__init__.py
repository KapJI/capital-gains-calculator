"""Parse ERI input files.

Excess Reported Income are yearly report provided by offshore fund managers to HMRC for
taxation purpsoes.
They report for each fund the amount of excess income has to be reported for taxation
perspective.

Full list of reporting funds at: https://www.gov.uk/government/publications/offshore-funds-list-of-reporting-funds
"""

from __future__ import annotations

from importlib import resources
import logging
from typing import TYPE_CHECKING

from cgt_calc.const import ERI_RESOURCE_FOLDER
from cgt_calc.resources import RESOURCES_PACKAGE

from .raw import read_eri_raw

if TYPE_CHECKING:
    from pathlib import Path

    from .model import EriTransaction

LOGGER = logging.getLogger(__name__)


def read_eri_transactions(
    eri_raw_file: Path | None,
) -> list[EriTransaction]:
    """Read Excess Reported Income transactions for all funds."""
    transactions = []

    for file in (
        resources.files(RESOURCES_PACKAGE).joinpath(ERI_RESOURCE_FOLDER).iterdir()
    ):
        if file.is_file() and file.name.endswith(".csv"):
            transactions += read_eri_raw(file)

    if eri_raw_file is not None:
        transactions += read_eri_raw(eri_raw_file)
    else:
        LOGGER.debug("No ERI raw file provided")

    return transactions
