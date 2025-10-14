#!/usr/bin/env bash

# Generate example CGT report.
# Usage: run with no arguments.

set -euo pipefail

# Change to project root
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Update dependencies
uv sync

uv run cgt-calc \
  --year 2020 \
  --schwab tests/schwab/data/schwab_transactions.csv \
  --trading212 tests/trading212/data/ \
  --mssb tests/morgan_stanley/data/ \
  --output docs/example_report.pdf
