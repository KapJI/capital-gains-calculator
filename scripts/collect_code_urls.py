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


def _iter_urls(source: str, filename: str) -> Iterator[tuple[str, int]]:
    """Yield each documentation URL in source with the line holding it."""
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for match in DOCS_URL.findall(node.value):
                yield match, node.lineno

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
