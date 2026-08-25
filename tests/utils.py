"""Utils for tests."""

import os
import sys

import pytest


def build_cmd(*args: str, keep_tex: bool = False) -> list[str]:
    """Return CLI command for cgt_calc with optional pdflatex disabled.

    Pass keep_tex when the test reads the generated .tex. Rendering a PDF puts
    the LaTeX source in a temporary file and leaves no .tex next to the report,
    so a test asserting on the source has to skip pdflatex even where
    ENABLE_PDFLATEX asks for it.
    """
    cmd = [sys.executable, "-m", "cgt_calc.main", *args]
    if keep_tex or not os.getenv("ENABLE_PDFLATEX"):
        cmd.append("--no-pdflatex")
    cmd += ["--exchange-rates-file", "tests/exchange_rates_data.csv"]
    return cmd


def report_path(request: pytest.FixtureRequest) -> str:
    """Return an --output path named after the test asking for it.

    Reports land in one directory and CI keeps them as an artifact, so a
    downloaded file has to say which test produced it. Hyphens because the
    workflow collects them with an `out/test-*` glob.
    """
    return f"out/{request.node.name.replace('_', '-')}/"


def stderr_alerts(stderr: str) -> list[str]:
    """Return warning/error lines from stderr, ignoring info chatter."""
    return [
        line
        for line in stderr.splitlines()
        if line.startswith(("WARNING", "ERROR", "CRITICAL"))
    ]
