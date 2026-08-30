"""Collect the documentation URLs written in the cgt_calc sources.

lychee reads Markdown, so a URL that lives in a Python file is never checked.
Scanning the source with a regular expression alone is not enough either:
most of these URLs are split across two adjacent string literals, and a match
that stops at the closing quote silently drops the anchor. Strings are read
through ast, which folds the literals back into one constant, and comments
through the token stream, which ast discards.

Writes a Markdown list to stdout for scripts/check_links.sh to hand to
lychee, and each URL's source location to stderr so a reported URL can be
traced back to the line that holds it. Joining the literals textually would
lose that line, and a concatenated URL cannot be grepped for afterwards.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path
import re
import sys
import tokenize
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_ROOT = Path("cgt_calc")
DOCS_URL = re.compile(r"https://cgt-calc\.uk/\S+")
# A URL written at the end of a sentence keeps the punctuation that ended it.
TRAILING_PUNCTUATION = ".,;:"


def _string_urls(node: ast.Constant, source: str) -> Iterator[tuple[str, int]]:
    """Yield the URLs in a string node with the line each one starts on.

    A node reports the line its string opens on, which for a docstring is
    often far above the URL. Counting newlines in the value would drift on
    the escaped-newline style the error messages use, where the value breaks
    a line the source does not, so the offsets are counted in the source text
    instead. Both scans run in document order, so a URL split across two
    literals - which the source scan sees only as far as the closing quote -
    still lands on the line it starts on.
    """
    if not isinstance(node.value, str):
        return
    matches = DOCS_URL.findall(node.value)
    if not matches:
        return
    segment = ast.get_source_segment(source, node) or ""
    starts = [
        segment[: found.start()].count("\n") for found in DOCS_URL.finditer(segment)
    ]
    for index, match in enumerate(matches):
        yield match, node.lineno + (starts[index] if index < len(starts) else 0)


def _iter_urls(source: str, filename: str) -> Iterator[tuple[str, int]]:
    """Yield each documentation URL in source with the line holding it."""
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Constant):
            yield from _string_urls(node, source)

    # Comments never reach the syntax tree, so they are read from the tokens.
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            for match in DOCS_URL.findall(token.string):
                yield match, token.start[0]


def collect_urls(root: Path) -> dict[str, str]:
    """Map every documentation URL under root to where it was found."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match, lineno in _iter_urls(source, str(path)):
            found.setdefault(match.rstrip(TRAILING_PUNCTUATION), f"{path}:{lineno}")
    return found


def main() -> None:
    """Print the URLs as a Markdown list, and their locations to stderr."""
    for url, location in sorted(collect_urls(PACKAGE_ROOT).items()):
        print(f"{location} -> {url}", file=sys.stderr)
        print(f"- <{url}>")


if __name__ == "__main__":
    main()
