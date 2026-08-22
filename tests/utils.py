"""Utils for tests."""

import os
import sys

import pytest


def build_cmd(*args: str) -> list[str]:
    """Return CLI command for cgt_calc with optional pdflatex disabled."""
    cmd = [sys.executable, "-m", "cgt_calc.main", *args]
    if not os.getenv("ENABLE_PDFLATEX"):
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
