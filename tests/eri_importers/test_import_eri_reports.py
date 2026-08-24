"""Tests for the ERI resource import script."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, override

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from cgt_calc.exceptions import InvalidTransactionError
from cgt_calc.model import CurrencyCode, Isin
from cgt_calc.parsers.eri.importer.model import ERIImporter, ERIImporterOutput
from cgt_calc.parsers.eri.model import ERITransaction
from scripts import import_eri_reports


class EmptyImporter(ERIImporter):
    """Importer stub that recognises reports but finds no current transactions."""

    def __init__(self, seen: list[Path]) -> None:
        """Store parsed paths for assertions."""
        super().__init__(name="Empty")
        self.seen = seen

    @override
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Recognise the file and return an intentionally empty result."""
        self.seen.append(file)
        return ERIImporterOutput([], "unused.csv")


def test_directory_import_continues_after_empty_importer_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An old report does not prevent later files in the directory being visited."""
    reports = [tmp_path / "old.pdf", tmp_path / "next.pdf"]
    for report in reports:
        report.touch()
    seen: list[Path] = []
    monkeypatch.setattr(import_eri_reports, "ERI_IMPORTERS", [EmptyImporter(seen)])

    import_eri_reports.eri_import_from_path(str(tmp_path))

    assert set(seen) == set(reports)
    assert capsys.readouterr().out.count("WARNING: ERI importer Empty") == 2


@pytest.mark.parametrize("price", [Decimal(-1), Decimal("NaN"), Decimal("Infinity")])
def test_unusable_eri_price_is_rejected_before_it_is_written(price: Decimal) -> None:
    """A price the calculator would refuse must not reach the resource files."""
    transaction = ERITransaction(
        date=datetime.date(2024, 6, 30),
        isin=Isin("IE00B3RBWM25"),
        price=price,
        currency=CurrencyCode("USD"),
    )

    with pytest.raises(InvalidTransactionError, match="finite and non-negative"):
        import_eri_reports.validate_and_remove_duplicates([transaction])
