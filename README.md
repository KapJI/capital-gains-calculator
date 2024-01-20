[![CI](https://github.com/KapJI/capital-gains-calculator/actions/workflows/ci.yml/badge.svg)](https://github.com/KapJI/capital-gains-calculator/actions)
[![PyPI version](https://img.shields.io/pypi/v/cgt-calc)](https://pypi.org/project/cgt-calc/)

# UK capital gains calculator

Calculate capital gains tax by transaction history exported from Charles Schwab, Trading 212 and Morgan Stanley. Generate PDF report with calculations.

Automatically convert all prices to GBP and apply HMRC rules to calculate capital gains tax: "same day" rule, "bed and breakfast" rule, section 104 holding.

## Report example

[calculations_example.pdf](https://github.com/KapJI/capital-gains-calculator/blob/main/calculations_example.pdf)

## Installation

Install it with [pipx](https://pypa.github.io/pipx/) (or regular pip):

```shell
pipx install cgt-calc
```

## Prerequisites

-   Python 3.8 or above.
-   `pdflatex` is required to generate the report.

## Install LaTeX

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

You will need several input files:

-   Exported transaction history from Schwab in CSV format since the beginning.
    Or at least since you first acquired the shares, which you were holding during the tax year. Schwab only allows to download transaction for the last 4 years so keep it safe. After that you may need to restore transactions from PDF statements.
    [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/test_data/schwab_transactions.csv).
-   Exported transaction history from Schwab Equity Awards (e.g. for Alphabet/Google employees) since the beginning. These are to be downloaded in JSON format. Instructions are available at the top of the [parser file](../main/cgt_calc/parsers/schwab_equity_award_json.py).
-   Exported transaction history from Trading 212.
    You can use several files here since Trading 212 limit the statements to 1 year periods.
    [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/test_data/trading212).
-   Exported transaction history from Morgan Stanley.
    Since Morgan Stanley generates multiple files in a single report, please specify a directory produced from the report download page.
-   Exported transaction history from Sharesight
    Sharesight is a portfolio tracking tool with support for multiple brokers.

    You will need the "All Trades" and "Taxable Income" reports since the beginning.
    Make sure to select "Since Inception" for the period, and "Not Grouping".
    Export both reports to Excel or Google Sheets, save as CSV, and place them in the same folder.

    Sharesight aggregates transactions from multiple brokers, but doesn't necessarily have balance information.
    Use the `--no-balance-check` flag to avoid spurious errors.

    Since there is no direct support for equity grants, add `Stock Activity` as part of the comment associated with any vesting transactions - making sure they have the grant price filled.

    [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/test_data/sharesight).

-   CSV file with initial stock prices in USD at the moment of vesting, split, etc.
    [`initial_prices.csv`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_prices.csv) comes pre-packaged, you need to use the same format.
-   (Optional) Monthly exchange rates prices from [gov.uk](https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat).
    `exchange_rates.csv` gets generated automatically using HMRC API, you need to use the same format if you want to override it.

Then run (you can omit the brokers you don't use):

```shell
cgt-calc --year 2020 --schwab schwab_transactions.csv --trading212 trading212/ --mssb mmsb_report/
```

See `cgt-calc --help` for the full list of settings.

## Disclaimer

Please be aware that I'm not a tax adviser so use this data at your own risk.

## Contribute

All contributions are highly welcomed.
If you notice any bugs please open an issue or send a PR to fix it.

Feel free to add new parsers to support transaction history from more brokers.

## Testing

This project uses [Poetry](https://python-poetry.org/) for managing dependencies.

-   For local testing you need to [install it](https://python-poetry.org/docs/#installation).
-   After that run `poetry install` to install all dependencies.
-   Then activate `pre-commit` hook: `poetry run pre-commit install`

You can also run all linters and tests manually with this command:

```shell
poetry run pre-commit run --all-files
```
