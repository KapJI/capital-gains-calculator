"""Tests for the documentation URL collector used by the link check."""

from __future__ import annotations

from pathlib import Path

from scripts.collect_code_urls import PACKAGE_ROOT, collect_urls

REPO_ROOT = Path(__file__).parents[2]


def test_url_split_across_string_literals_is_recovered(tmp_path: Path) -> None:
    """Implicit concatenation must not truncate a URL at the closing quote."""
    (tmp_path / "message.py").write_text(
        'MESSAGE = (\n    "See https://cgt-calc.uk/brokers/raw/"\n    "#a-section"\n)\n',
        encoding="utf-8",
    )

    assert collect_urls(tmp_path) == {
        "https://cgt-calc.uk/brokers/raw/#a-section": f"{tmp_path / 'message.py'}:2"
    }


def test_docstring_url_reports_its_own_line(tmp_path: Path) -> None:
    """A string node reports where it opens, which is not where the URL is."""
    (tmp_path / "doc.py").write_text(
        '"""Summary.\n'
        "\n"
        "Prose that runs on for long enough to matter.\n"
        "\n"
        "See https://cgt-calc.uk/usage/#check-the-result for the checklist.\n"
        '"""\n',
        encoding="utf-8",
    )

    assert collect_urls(tmp_path) == {
        "https://cgt-calc.uk/usage/#check-the-result": f"{tmp_path / 'doc.py'}:5"
    }


def test_url_in_a_comment_is_collected(tmp_path: Path) -> None:
    """Comments are dropped by ast, so they are read from the token stream."""
    (tmp_path / "note.py").write_text(
        "# See https://cgt-calc.uk/docker/#volumes\nVALUE = 1\n", encoding="utf-8"
    )

    assert collect_urls(tmp_path) == {
        "https://cgt-calc.uk/docker/#volumes": f"{tmp_path / 'note.py'}:1"
    }


def test_sentence_punctuation_is_not_part_of_the_url(tmp_path: Path) -> None:
    """A URL ending a sentence keeps the full stop, which is not part of it."""
    (tmp_path / "message.py").write_text(
        'MESSAGE = "See https://cgt-calc.uk/configuration/."\n', encoding="utf-8"
    )

    assert set(collect_urls(tmp_path)) == {"https://cgt-calc.uk/configuration/"}


def test_package_sources_yield_urls() -> None:
    """An empty result would let the link check pass without checking anything.

    PACKAGE_ROOT is relative because check_links.sh runs from the repository
    root, so it is resolved here rather than relying on the test's directory.
    """
    assert collect_urls(REPO_ROOT / PACKAGE_ROOT)
