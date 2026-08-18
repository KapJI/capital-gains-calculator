"""Utils for tests."""

import os
import sys


def build_cmd(*args: str) -> list[str]:
    """Return CLI command for cgt_calc with optional pdflatex disabled."""
    cmd = [sys.executable, "-m", "cgt_calc.main", *args]
    if not os.getenv("ENABLE_PDFLATEX"):
        cmd.append("--no-pdflatex")
    cmd += ["--exchange-rates-file", "tests/exchange_rates_data.csv"]
    return cmd


def stderr_alerts(stderr: str) -> list[str]:
    """Return warning/error lines from stderr, ignoring info chatter."""
    return [
        line
        for line in stderr.splitlines()
        if line.startswith(("WARNING", "ERROR", "CRITICAL"))
    ]
