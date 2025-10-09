"""Module that is able to import ERI data into the tool resources."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
import site
from typing import TYPE_CHECKING

from cgt_calc.const import INITIAL_ISIN_TRANSLATION_FILE
from cgt_calc.exceptions import InvalidTransactionError
import cgt_calc.resources as RESOURCES
from cgt_calc.util import approx_equal

from .blackrock import BlackrockParser
from .raw import COLUMNS, RAW_DATE_FORMAT, read_eri_raw
from .vanguard import VanguardParser

if TYPE_CHECKING:
    import datetime

    from .model import EriParser, EriTransaction

ERI_PARSERS: list[EriParser] = [BlackrockParser(), VanguardParser()]


def is_running_in_site_packages() -> bool:
    """Check if we're running from a site-packages directory."""
    current_file_path = Path(__file__).resolve()

    # Get all site-packages directories in the current environment
    site_packages_paths = site.getsitepackages()
    if site.ENABLE_USER_SITE:
        site_packages_paths.append(site.getusersitepackages())

    for site_path in site_packages_paths:
        if Path(site_path).resolve() in current_file_path.parents:
            return True
    return False


def validate_and_remove_duplicates(
    transactions: list[EriTransaction],
) -> list[EriTransaction]:
    """Validate and remove duplicate transactions.

    Sort the final output by date for writing
    """
    transaction_index: dict[tuple[str, datetime.date], EriTransaction] = {}
    result = []
    for transaction in transactions:
        assert transaction.price is not None, (
            f"Transaction price not set for {transaction}"
        )
        assert transaction.isin, f"Transaction ISIN not set for {transaction}"
        key = (transaction.isin, transaction.date)
        if key in transaction_index:
            current_transaction = transaction_index[key]
            assert current_transaction.price is not None, str(current_transaction)
            if approx_equal(
                current_transaction.price, transaction.price, Decimal("0.0001")
            ):
                continue
            raise InvalidTransactionError(
                transaction,
                "Duplicate same day ERI with different price from "
                f"{current_transaction}",
            )
        result.append(transaction)
        transaction_index[key] = transaction

    result.sort(key=lambda t: t.date)
    return result


def eri_import_from_file(path: Path) -> None:
    """Import the specified path into the tool resources."""

    assert path.is_file(), f"Specified path {path} not a file!"

    print(f"Processing ERI file: {path}")
    for parser in ERI_PARSERS:
        data = parser.parse(path)
        if data is None:
            continue

        assert data, f"ERI Parser {parser.name} emitted not transactions for {path}"
        print(
            f"ERI file {path} successfully parsed with {parser.name}, "
            f"transactions found: {len(data.transactions)}"
        )
        output_path = Path(RESOURCES.__file__).parent / "eri" / data.output_file_name
        transactions = validate_and_remove_duplicates(
            read_eri_raw(output_path) + data.transactions
        )

        with output_path.open("w", encoding="utf8") as fout:
            data_rows = [
                [
                    transaction.isin,
                    transaction.date.strftime(RAW_DATE_FORMAT),
                    transaction.currency,
                    transaction.price,
                ]
                for transaction in transactions
            ]
            writer = csv.writer(fout)
            writer.writerows([COLUMNS, *data_rows])
        return

    print(f"WARNING: No ERI parser found for {path}")


def eri_import_from_path(path_str: str) -> str:
    """Import the specified path (file or d) into the tool resources.

    Returns the path of the ISIN translation file to use for writing into resources
    """
    assert not is_running_in_site_packages(), (
        "You can't import files inside an installation of cgt_calc",
        "Please repeat this operation from the repository folder",
    )
    print(f"ERI data import mode activated from {path_str}")
    path = Path(path_str)
    if path.is_dir():
        for subpath in path.rglob("*"):
            if subpath.is_file() and not subpath.name.startswith("."):
                eri_import_from_file(subpath)
    else:
        eri_import_from_file(path)
    return str(
        (Path(RESOURCES.__file__).parent / INITIAL_ISIN_TRANSLATION_FILE).resolve()
    )
