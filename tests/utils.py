"""Utils for tests."""

import os
import sys


def build_cmd(*args: str) -> list[str]:
    """Return CLI command for cgt_calc with optional pdflatex disabled."""
    cmd = [sys.executable, "-m", "cgt_calc.main", *args]
    if not os.getenv("ENABLE_PDFLATEX"):
        cmd.append("--no-pdflatex")
    return cmd
