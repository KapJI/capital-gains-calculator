# Extra Data and Options

Most users can generate a report from a supported broker export without these files or options.
Start with your [broker guide](brokers/index.md). Return here only if cgt-calc asks for more
information or one of the tax settings below applies to your investments.

## Automatic data fetching

cgt-calc creates and updates these local files itself. You do not need to create or select them for
a normal calculation.

### Exchange rates

cgt-calc downloads monthly GBP exchange rates from the
[UK Trade Tariff API](https://www.trade-tariff.service.gov.uk/exchange_rates) for 2021 onwards and
[HMRC's legacy service](https://www.hmrc.gov.uk/softwaredevelopers/2020-exrates.html) for earlier
periods. It saves them to `out/exchange_rates.csv`.

Use `--exchange-rates-file` to select another file in the same format. An empty value disables the
cache. Keep the completed file with the report to preserve the exchange rates used. cgt-calc may add
missing months to the selected file, so retain the version used for the final report. This does not
preserve other fetched data, such as Yahoo Finance prices.

### ISIN to ticker translation

When an ERI row identifies a fund only by its ISIN, cgt-calc maps it to ticker symbols. It checks
existing mappings first and queries the [Open FIGI API](https://www.openfigi.com/api/overview) only
if needed. It saves new mappings in `out/isin_translation.csv` by default; `--isin-translation-file`
selects another path.

cgt-calc can create or rewrite this file after a successful lookup or after learning a mapping from
broker transactions. It starts with the bundled
[`initial_isin_translation.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv).
If you edit the cache, a row for an existing ISIN replaces its bundled symbols. Put every verified
ticker for that ISIN on the same row.

## When extra information is needed

### Missing stock-plan prices

A vest or other stock-plan acquisition needs a price per share to establish its cost. Broker files
usually provide it, and cgt-calc includes some historical values in
[`initial_prices.csv`](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/initial_prices.csv).

If cgt-calc stops with a **No initial price** error, create a CSV in the same format with the
missing USD price and pass it with `--initial-prices-file`.

### Spin-off source mappings

A spin-off row may name the new holding without naming the old holding it came from. cgt-calc needs
that link to divide the existing pooled cost between them. During an interactive run, it asks for
the old ticker and saves the answer to `out/spin_offs.csv`.

If the run cannot ask, it stops and tells you which `dst,src` row to add. Use `--spin-offs-file` to
select another file for these mappings.

### Estimate unrealized gains

Use `--unrealized-gains` to add current-price estimates to the portfolio and summary printed in the
terminal. For each holding left at the report's end date, cgt-calc fetches today's price from Yahoo
Finance, converts it to GBP and subtracts its Section 104 pooled cost. It warns when a price is
unavailable.

This is a portfolio estimate, not the gain from an actual sale. It does not include selling costs or
change the report's capital gains, losses or taxable figures. For an older report, the ending
portfolio may not be what you hold today. See
[Privacy and data security](privacy.md#privacy-and-data-security) for details of the Yahoo lookup.

### Bond-fund income

Some offshore bond funds have income that must be reported as interest rather than dividends. After
checking the fund's classification, pass its ticker to `--interest-fund-tickers`. Use a
comma-separated list for several tickers. This setting applies to both cash distributions and ERI;
see the [offshore-fund checklist](offshore-funds.md#what-to-check). It reports the income as foreign
interest, so do not use it for a UK fund. If cgt-calc reports a UK bond fund distribution as a
dividend, adjust the income figures outside cgt-calc.

### CGT-exempt instruments (advanced)

Most users should not use `--cgt-exempt-tickers`. Pass a comma-separated list only when you have
established that each instrument is exempt under
[TCGA 1992 s115](https://www.legislation.gov.uk/ukpga/1992/12/section/115).

- This is your own unverified assertion. The calculator does not identify or validate gilts or
    [qualifying corporate bonds (QCBs)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg53702),
    and QCB status cannot be inferred from a ticker or name.
- The override disregards both gains and losses on disposals. Do not use it where a disposal can
    still produce a charge: disposing of a QCB acquired on a takeover or reorganisation can bring a
    [deferred gain](https://www.gov.uk/government/publications/share-reorganisations-company-takeovers-and-capital-gains-tax-hs285-self-assessment-helpsheet)
    back into charge, and the profit on a
    [deeply discounted security](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim3010)
    is taxable as income. Neither is calculated here.
- The per-disposal breakdown in the PDF applies the ordinary same-day, 30-day and Section 104 share
    identification rules. Those are not the identification rules HMRC gives for gilts, QCBs and
    other relevant securities, so read the breakdown as workings for the quantities only. It has no
    effect on the figures reported, because the gain or loss is disregarded.
- Coupon and accrued interest remain taxable income. The calculator does not implement the
    [Accrued Income Scheme](https://www.gov.uk/government/publications/accrued-income-scheme-hs343-self-assessment-helpsheet).
- The importers for [Freetrade](brokers/freetrade.md),
    [Hargreaves Lansdown](brokers/hargreaves-lansdown.md) and
    [Interactive Brokers](brokers/interactive-brokers.md) are documented as unvalidated for bonds
    and gilts. This override does not validate them either.
- The override changes the capital gains calculation only. A coupon is reported in whichever box its
    broker rows put it in. `--interest-fund-tickers` reports as foreign interest, so it is not a fix
    for a UK gilt coupon. Check the interest and dividend figures against your own records.
- Matching uses the ticker at the disposal, so an instrument renamed part-way through the history
    has to be listed under both names.
- A gift of a listed instrument is reported as an exempt disposal and does not also appear in the
    **Gifts at market value** section.
- Listing an instrument does not lift the restrictions on disposing of it in more than one way on a
    single day. A sale and a gift to a connected person on the same day are still refused, even
    though the loss they cannot apportion would be disregarded.
