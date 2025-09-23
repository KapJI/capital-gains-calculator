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

-   Python 3.9 or above.
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

-   You need to supply transaction history for each account you have. See below for per-broker instructions. The history needs to contain all transactions since the beginning, or at least since you first acquired the shares owned during the relevant tax years.
-   Once you have all your transactions from all your brokers you need to supply them together, for example to generate the report for the tax year 2020/21:

```shell
cgt-calc --year 2020 --schwab schwab_transactions.csv --trading212 trading212/ --mssb mmsb_report/
```
cberrbblucd
-   Run `cgt-calc --help` for the full list of settings.
-   If your broker is not listed below you can still try to use the raw format. We also welcome PRs for new parsers.

## Broker-specific instructions

<details>
    <summary>🔍 Instructions for broker "Charles Schwab"</summary>

You will need:

-   **Exported transaction history in CSV format.**
    Schwab only allows to download transaction for the last 4 years. If you require more, you can download the history in 4-year chunks and combine them.
    [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/test_data/schwab_transactions.csv).
-   **Exported transaction history from Schwab Equity Awards in CSV format.**
    Only applicable if you receive equity awards in your account (e.g. for Alphabet/Google employees). Follow the same procedure as in the normal transaction history but selecting your Equity Award account.

Example usage for the tax year 2020/21:

```shell
cgt-calc --year 2020 --schwab schwab_transactions.csv --schwab-award schwab_awards.csv
```

_Note: For historic reasons, it is possible to provide the Equity Awards history in JSON format with `--schwab_equity_award_json`. Instructions are available at the top of this [parser file](../main/cgt_calc/parsers/schwab_equity_award_json.py). Please use the CSV method above if possible._

</details>
 <br />
<details>
    <summary>🔍 Instructions for broker "Trading212"</summary>

You will need:

-   **Exported transaction history from Trading 212.**
    You can provide a folder containing several files since Trading 212 limit the statements to 1 year periods.
    [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/test_data/trading212).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --trading212 trading212_trxs_dir/
```

</details>
 <br />
<details>
    <summary>🔍 Instructions for broker "Morgan Stanley"</summary>

You will need:

-   **Exported transaction history from Morgan Stanley.**
    Since Morgan Stanley generates multiple files in a single report, please specify a directory produced from the report download page.
    [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/test_data/mssb).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --mssb morgan_stanley_trxs_dir/
```

</details>
 <br />
<details>
    <summary>🔍 Instructions for broker "Sharesight"</summary>

You will need:

-   **Exported transaction history from Sharesight.**
    Sharesight is a portfolio tracking tool with support for multiple brokers.
    -   You will need the "All Trades" and "Taxable Income" reports since the beginning. Make sure to select "Since Inception" for the period, and "Not Grouping".
    -   Export both reports to Excel or Google Sheets, save as CSV, and place them in the same folder.
    -   [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/test_data/sharesight).

Comments:

-   Sharesight aggregates transactions from multiple brokers, but doesn't necessarily have balance information.
    Use the `--no-balance-check` flag to avoid spurious errors.

-   Since there is no direct support for equity grants, add `Stock Activity` as part of the comment associated with any vesting transactions - making sure they have the grant price filled ([see example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/test_data/sharesight)).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --no-balance-check --sharesight sharesight_trxs_dir/
```

</details>
 <br />
<details>
    <summary>🔍 Instructions for broker "Vanguard"</summary>

You will need:

-   **Exported transaction history from Vanguard.**
    Vanguard can generate a report in Excel format with all transactions across all periods of time and all accounts (ISA, GA, etc). Grab the ones you're interested into (normally GA account) and put them in a single CSV file.
    [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/test_data/vanguard/report.csv).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --vanguard vanguard.csv
```

</details>
 <br />
<details>
    <summary>🔍 Instructions for RAW format</summary>

You will need:

-   **CSV using the RAW format.** If your broker isn't natively supported you might choose to convert whatever report you can produce into this basic format. 
    [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/test_data/raw/test_data.csv)

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --raw sharesight_trxs_dir/
```

</details>
 <br />

### Extra files that might be needed

-   **CSV file with initial stock prices in USD.** This is needed under special circumstances for example at the moment of vesting, split, etc.
    [`initial_prices.csv`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_prices.csv) comes pre-packaged, you need to use the same format. The program will inform when some required price is missing.
-   **(Automatic) Monthly exchange rates prices from [gov.uk](https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat).** This is needed to convert foreign currencies into GBP amounts. `exchange_rates.csv` gets generated automatically using HMRC API, you need to use the same format if you want to override it.
-   **Spin-off file.** Supplies extra information needed for spin-offs transactions through `--spin-offs-file`.

## Docker

These steps will build and run the calculator in a self-contained environment, in case you would rather not have a systemwide LaTeX installation (or don't want to interfere with an existing one).
The following steps are tested on an Apple silicon Mac and may need to be slightly modified on other platforms.
With the cloned repository as the current working directory:

```shell
$ docker buildx build --platform linux/amd64 --tag capital-gains-calculator .
```

Now you've built and tagged the calculator image, you can drop into a shell with `cgt-calc` installed on `$PATH`. Navigate to where you store your transaction data, and run:

```shell
$ cd ~/Taxes/Transactions
$ docker run --rm -it -v "$PWD":/data capital-gains-calculator:latest
a4800eca1914:/data# cgt-calc [...]
```

This will create a temporary Docker container with the current directory on the host (where your transaction data is) mounted inside the container at `/data`. Follow the usage instructions below as normal,
and when you're done, simply exit the shell. You will be dropped back into the shell on your host, with your output report pdf etc..

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
