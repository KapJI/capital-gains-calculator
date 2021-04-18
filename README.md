# UK capital gains calculator

[![CI](https://github.com/KapJI/capital_gains_calculator/actions/workflows/ci.yml/badge.svg)](https://github.com/KapJI/capital_gains_calculator/actions)
[![PyPI version](https://img.shields.io/pypi/v/cgt-calc)](https://pypi.org/project/cgt-calc/)

Calculate capital gains tax by transaction history exported from Schwab/Trading212 and generate PDF report with calculations.

Automatically convert all prices to GBP and apply HMRC rules to calculate capital gains tax: "same day" rule, "bed and breakfast" rule, section 104 holding.

## Report example

[calculations_example.pdf](https://github.com/KapJI/capital_gains_calculator/blob/main/calculations_example.pdf)

## Installation

Install it with [pipx](https://pipxproject.github.io/pipx/) (or regular pip):

```shell
pipx install cgt-calc
```

**`pdflatex` is required to generate the report.**

### MacOS

```shell
brew install --cask mactex-no-gui
```

### Debian based

```shell
apt install texlive-latex-base
```

### Windows

[Install MiKTeX.](https://miktex.org/download)

## Usage

-   `schwab_transactions.csv`: the exported transaction history from Schwab since the beginning. Or at least since you first acquired the shares, which you were holding during the tax year. You can probably convert transactions from other brokers to Schwab format.
-   `trading212/`: the exported transaction history from Trading212 since the beginning. Or at least since you first acquired the shares, which you were holding during the tax year. You can put several files here since Trading212 limit the statements to 1 year periods.
-   `GBP_USD_monthly_history.csv`: monthly GBP/USD prices from [gov.uk](https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat).
-   `initial_prices.csv`: stock prices in USD at the moment of vesting, split, etc.
-   Run `cgt-calc --tax_year 2020 --schwab schwab_transactions.csv --trading212 trading212/` (you can omit the brokers you don't use)
-   Use `cgt-calc --help` for more details/options.

## Disclaimer

Please be aware that I'm not a tax adviser so use this data at your own risk.

## Contribute

All contributions are highly welcomed.
If you notice any bugs please open an issue or send a PR to fix it.

Feel free to add parsers to support transaction history from more brokers.

## Testing

This project uses [Poetry](https://python-poetry.org/) for managing dependencies.

-   To test it locally you need to [install it](https://python-poetry.org/docs/#installation).
-   After that run `poetry install` to install all dependencies.
-   Then activate `pre-commit` hook: `poetry run pre-commit install`

You can also run all linters and tests manually with this command:

```shell
poetry run pre-commit run --all-files
```
