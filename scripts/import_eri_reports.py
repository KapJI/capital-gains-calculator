"""Module that is able to import ERI data into the tool resources."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from cgt_calc import resources
from cgt_calc.const import DEFAULT_ISIN_TRANSLATION_FILE
from cgt_calc.exceptions import InvalidTransactionError
from cgt_calc.parsers.eri.importer.blackrock import BlackrockImporter
from cgt_calc.parsers.eri.importer.vanguard import VanguardImporter
from cgt_calc.parsers.eri.raw import COLUMNS, RAW_DATE_FORMAT, ERIRawParser
from cgt_calc.util import approx_equal

if TYPE_CHECKING:
    import datetime

    from cgt_calc.parsers.eri.model import ERIImporter, ERITransaction

ERI_IMPORTERS: list[ERIImporter] = [BlackrockImporter(), VanguardImporter()]


def validate_and_remove_duplicates(
    transactions: list[ERITransaction],
) -> list[ERITransaction]:
    """Validate and remove duplicate transactions.

    Sort the final output by date for writing
    """
    transaction_index: dict[tuple[str, datetime.date], ERITransaction] = {}
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
    for importer in ERI_IMPORTERS:
        data = importer.parse(path)
        if data is None:
            continue

        assert data, f"ERI Importer {importer.name} emitted not transactions for {path}"
        print(
            f"ERI file {path} successfully parsed with {importer.name}, "
            f"transactions found: {len(data.transactions)}"
        )
        output_path = Path(resources.__file__).parent / "eri" / data.output_file_name
        transactions = []
        if output_path.exists():
            transactions += ERIRawParser.load_from_file(output_path)
        transactions += data.transactions
        transactions = validate_and_remove_duplicates(transactions)

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

    print(f"WARNING: No ERI importer found for {path}")


def eri_import_from_path(path_str: str) -> str:
    """Import the specified path (file or d) into the tool resources.

    Returns the path of the ISIN translation file to use for writing into resources
    """
    print(f"ERI data import mode activated from {path_str}")
    path = Path(path_str)
    if path.is_dir():
        for subpath in path.rglob("*"):
            if subpath.is_file() and not subpath.name.startswith("."):
                eri_import_from_file(subpath)
    else:
        eri_import_from_file(path)
    return str(
        (Path(resources.__file__).parent / DEFAULT_ISIN_TRANSLATION_FILE).resolve()
    )


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(description="Import ERI Reports in CGT tool")
    parser.add_argument(
        "eri_reports",
        type=str,
        help=(
            "Input file or folder to import ERI reports into "
            "the project resources folder using the existing ERI parsers available."
        ),
    )

    args = parser.parse_args()
    eri_import_from_path(args.eri_reports)
    print("Import complete! Run cgt_calc to use the imported data in your reports!")


if __name__ == "__main__":
    main()
