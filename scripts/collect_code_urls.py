"""Collect the documentation URLs embedded in cgt_calc source strings.

lychee reads Markdown, so a URL that lives in a Python string is never
checked. Most of them are also split across two adjacent string literals, so
scanning the source with a regular expression finds only the part before the
closing quote and silently drops the anchor. Parsing with ast folds the
literals back into one constant, so the whole URL is matched.

Writes a Markdown list to stdout for scripts/check_links.sh to hand to
lychee, and each URL's source location to stderr so a reported URL can be
traced back to the line that holds it.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

PACKAGE_ROOT = Path("cgt_calc")
DOCS_URL = re.compile(r"https://cgt-calc\.uk/\S+")
# A URL written at the end of a sentence keeps the punctuation that ended it.
TRAILING_PUNCTUATION = ".,;:"


def collect_urls(root: Path) -> dict[str, str]:
    """Map every documentation URL under root to where it was found."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in DOCS_URL.findall(node.value):
                    url = match.rstrip(TRAILING_PUNCTUATION)
                    found.setdefault(url, f"{path}:{node.lineno}")
    return found


def main() -> None:
    """Print the URLs as a Markdown list, and their locations to stderr."""
    for url, location in sorted(collect_urls(PACKAGE_ROOT).items()):
        print(f"{location} -> {url}", file=sys.stderr)
        print(f"- <{url}>")


if __name__ == "__main__":
    main()
