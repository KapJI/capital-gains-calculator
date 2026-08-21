# Configuration Files

The following configuration files and options allow you to customize the calculator's behaviour:

## Automatic data fetching

- **Exchange rates.** Monthly GBP exchange rates are automatically downloaded from the
  [UK Trade Tariff API](https://www.trade-tariff.service.gov.uk/exchange_rates) and saved to
  `out/exchange_rates.csv`. You can override these by providing your own file in the same format
  using the `--exchange-rates-file` option.

- **ISIN to ticker translation.** When your broker doesn't provide ticker symbols, the tool
  automatically translates ISIN codes using the
  [Open FIGI API](https://www.openfigi.com/api/overview). This is used for calculating Excess
  Reportable Income (ERI) on offshore funds. The results are saved to `out/isin_translation.csv`.
  Pre-packaged mappings are available in
  [`initial_isin_translation.csv`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv),
  which you can extend using `--isin-translation-file` option.

## Manual configuration files

- **Initial stock prices.** Required for special events like vesting, splits, or spin-offs when
  historical prices aren't available from your broker. Prices should be in USD.
  [`initial_prices.csv`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_prices.csv)
  comes pre-packaged. The program will inform you when a required price is missing, and you can
  supply custom data with `--initial-prices-file`.

- **Spin-off transactions.** Provide additional details for spin-off events using the
  `--spin-offs-file` option.

- **Interest fund tickers.** Some bond funds and ETFs should be taxed as interest rather than
  dividends. Specify these funds via the `--interest-fund-tickers` CLI option, using a
  comma-separated list of ticker symbols.
