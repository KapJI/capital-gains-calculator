#!/usr/bin/env bash

# Generate the compact 2025/26 example CGT report.
# Usage: run with no arguments.

set -euo pipefail

# Change to project root
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Update dependencies
uv sync

uv run cgt-calc \
    --year 2025 \
    --schwab-file scripts/example_data/schwab_transactions.csv \
    --exchange-rates-file scripts/example_data/exchange_rates.csv \
    --output docs/assets/example_report.pdf
