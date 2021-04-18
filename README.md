# UK capital gains calculator

[![CI](https://github.com/KapJI/capital_gains_calculator/workflows/CI/badge.svg)](https://github.com/KapJI/capital_gains_calculator/actions)

Calculate capital gains tax by transaction history exported from Schwab/Trading212 and generate PDF report with calculations. Automatically convert all prices to GBP and apply HMRC rules to calculate capital gains tax: "same day" rule, "bed and breakfast" rule, section 104 holding.

## Report example

[calculations_example.pdf](https://github.com/KapJI/capital_gains_calculator/blob/main/calculations_example.pdf)

## Setup

On Mac:

```shell
brew install --cask mactex-no-gui
pip install -r requirements.txt
```

## Usage

-   `schwab_transactions.csv`: the exported transaction history from Schwab since the beginning. Or at least since you first acquired the shares, which you were holding during the tax year. You can probably convert transactions from other brokers to Schwab format.
-   `trading212/`: the exported transaction history from Trading212 since the beginning. Or at least since you first acquired the shares, which you were holding during the tax year. You can put several files here since Trading212 limit the statements to 1 year periods.
-   `GBP_USD_monthly_history.csv`: monthly GBP/USD prices from [gov.uk](https://www.gov.uk/government/collections/exchange-rates-for-customs-and-vat).
-   `initial_prices.csv`: stock prices in USD at the moment of vesting, split, etc.
-   Run `python3 calc.py --tax_year 2020 --schwab schwab_transactions.csv --trading212 trading212/` (you can omit the brokers you don't use)
-   Use `python3 calc.py --help` for more details/options.

## Testing

```shell
pip install pytest
pytest
```

## Disclaimer

Please be aware that I'm not a tax adviser so use this data at your own risk.

## Contribute

If you notice any bugs feel free to open an issue or send a PR.
