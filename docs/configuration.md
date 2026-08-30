# Configuration Files

The following configuration files and options allow you to customize the calculator's behaviour:

## Automatic data fetching

- **Exchange rates.** Monthly GBP exchange rates are automatically downloaded from the
    [UK Trade Tariff API](https://www.trade-tariff.service.gov.uk/exchange_rates) and saved to
    `out/exchange_rates.csv`. You can override these by providing your own file in the same format
    using the `--exchange-rates-file` option.

- **ISIN to ticker translation.** When an ERI row identifies a fund only by its ISIN, cgt-calc maps
    it to ticker symbols. It uses existing mappings first and queries the
    [Open FIGI API](https://www.openfigi.com/api/overview) only if needed. This is used for
    calculating Excess Reported Income (ERI) on offshore funds. The default read/write cache is
    `out/isin_translation.csv`; `--isin-translation-file` selects a different cache path. Existing
    entries are read, and cgt-calc can create or rewrite the file after a successful Open FIGI
    lookup or after learning a mapping from broker transactions. Pre-packaged mappings are available
    in
    [`initial_isin_translation.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv),
    which you can extend using the cache file. A cache entry for an existing ISIN replaces the
    bundled symbol set for that ISIN, so put every verified alias in additional columns on the same
    CSV row.

## Manual configuration files

- **Initial stock prices.** Required for special events like vesting, splits, or spin-offs when
    historical prices aren't available from your broker. Prices should be in USD.
    [`initial_prices.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_prices.csv)
    comes pre-packaged. The program will inform you when a required price is missing, and you can
    supply custom data with `--initial-prices-file`.

- **Spin-off transactions.** Provide additional details for spin-off events using the
    `--spin-offs-file` option.

- **Interest fund tickers.** Some bond funds and ETFs should be taxed as interest rather than
    dividends. Specify these funds via the `--interest-fund-tickers` CLI option, using a
    comma-separated list of ticker symbols.

- **CGT-exempt instrument classification (advanced manual override).** Use `--cgt-exempt-tickers`
    with a comma-separated list only when you have established that each instrument is exempt under
    [TCGA 1992 s115](https://www.legislation.gov.uk/ukpga/1992/12/section/115).

    - This is your own unverified assertion. The calculator does not identify or validate gilts or
        [qualifying corporate bonds (QCBs)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg53702),
        and QCB status cannot be inferred from a ticker or name.
    - The override disregards both gains and losses on disposals. Do not use it where a disposal can
        still produce a charge: disposing of a QCB acquired on a takeover or reorganisation can
        bring a
        [deferred gain](https://www.gov.uk/government/publications/share-reorganisations-company-takeovers-and-capital-gains-tax-hs285-self-assessment-helpsheet)
        back into charge, and the profit on a
        [deeply discounted security](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim3010)
        is taxable as income. Neither is calculated here.
    - The per-disposal breakdown in the PDF applies the ordinary same-day, 30-day and Section 104
        share identification rules. Those are not the identification rules HMRC gives for gilts,
        QCBs and other relevant securities, so read the breakdown as workings for the quantities
        only. It has no effect on the figures reported, because the gain or loss is disregarded.
    - Coupon and accrued interest remain taxable income. The calculator does not implement the
        [Accrued Income Scheme](https://www.gov.uk/government/publications/accrued-income-scheme-hs343-self-assessment-helpsheet).
    - The importers for [Freetrade](brokers/freetrade.md),
        [Hargreaves Lansdown](brokers/hargreaves-lansdown.md) and
        [Interactive Brokers](brokers/interactive-brokers.md) are documented as unvalidated for
        bonds and gilts. This override does not validate them either.
    - The override changes the capital gains calculation only. A coupon is reported in whichever box
        its broker rows put it in. `--interest-fund-tickers` reports as foreign interest, so it is
        not a fix for a UK gilt coupon. Check the interest and dividend figures against your own
        records.
    - Matching uses the ticker at the disposal, so an instrument renamed part-way through the
        history has to be listed under both names.
    - A gift of a listed instrument is reported as an exempt disposal and does not also appear in
        the **Gifts at market value** section.
    - Listing an instrument does not lift the restrictions on disposing of it in more than one way
        on a single day. A sale and a gift to a connected person on the same day are still refused,
        even though the loss they cannot apportion would be disregarded.
