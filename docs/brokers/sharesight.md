# Sharesight

cgt-calc reads two Sharesight reports: **All Trades** for acquisitions and disposals, and **Taxable
Income** for dividends and tax deducted from them. This is useful when Sharesight already combines
activity from several brokers into one GBP portfolio.

Use a Sharesight portfolio whose base currency is GBP. The importer treats local income and the
portfolio value of foreign-exchange trades as GBP.

## Export the reports

### All Trades

Export all trades from the portfolio's first transaction. Earlier acquisitions can still affect
disposals in the tax year you are reporting; see [Before you start](../usage.md#before-you-start).

1. Open **Tax** and select **All Trades Report**.
2. Set the date range to **Since inception**.
3. Select **Do not group (Holdings)**.
4. Export the report to a spreadsheet or Google Drive. Do not use the PDF export.
5. Open the exported spreadsheet and save or download the sheet containing the All Trades table as a
   CSV file.

See Sharesight's current
[All Trades Report instructions](https://help.sharesight.com/uk/trades_report/) for the report
controls.

### Taxable Income

1. Open the portfolio's **Taxable Income Report**.
2. Set the date range to the tax year you are calculating.
3. Export the report to a spreadsheet or Google Drive. Do not use the PDF export.
4. Open the exported spreadsheet and save or download the report sheet as a CSV file.

Sharesight recommends checking its income figures against your dividend statements because the
report relies on third-party data. Its
[Taxable Income Report instructions](https://help.sharesight.com/uk/taxable_income/) explain the
available periods and exports.

## Prepare the directory

Keep both CSV files directly in one directory, for example:

```text
sharesight/
├── All Trades Report.csv
└── Taxable Income Report.csv
```

Use the filenames shown above when saving the CSV files. cgt-calc does not read the original `.xlsx`
files or search subdirectories.

Do not rename or delete columns when converting the worksheets. In addition to the legacy headings,
cgt-calc accepts aliases including `Market Code`, `Qty`, `Instrument Currency` and `Exch. Rate`. You
can compare the legacy structure with the
[sanitised example reports](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/sharesight/data/inputs).

## Generate the report

For the 2024/25 tax year, run:

```shell
cgt-calc --year 2024 --sharesight-dir sharesight/ --no-balance-check
```

`--year 2024` means 6 April 2024 to 5 April 2025. `--no-balance-check` is needed because these two
reports do not contain the complete cash activity required to reconcile a broker balance. Follow
[Generate Your First Report](../usage.md) to find and check the output.

## Supported activity

The Sharesight importer currently handles:

| Report         | Included activity                                                                                               |
| -------------- | --------------------------------------------------------------------------------------------------------------- |
| All Trades     | `Buy` and `Sell` rows, including Sharesight foreign-exchange rows, and equity grants entered as described below |
| Taxable Income | Local and foreign dividend payments, including `Tax Deducted` and `Foreign Tax Deducted` amounts                |

### Equity grants

cgt-calc identifies an equity grant through the trade comment. To record a vest:

1. Record it as a `Buy` in Sharesight.
2. Enter the market value per share on the vest date as the price.
3. Include `Stock Activity` anywhere in the trade comment.

cgt-calc then treats the row as stock activity rather than a cash purchase. A `Sell` row carrying
that marker is rejected. If the CSV has no `Comments` column, cgt-calc warns that it cannot detect
these markers.

## Known limitations

- [`Opening Balance`](https://help.sharesight.com/opening-balance/) trades are not supported.
  Sharesight uses them when the original buy and sell records are unavailable, but cgt-calc needs
  the original acquisition dates and costs for UK share matching.
- `Split`, `Consolidation` and `Bonus` rows are not supported. They stop the import with an unknown
  action error. Do not delete such a row to make the calculation run: later quantities and gains
  could be wrong.
- Cash deposits, withdrawals and balances are not imported, which is why `--no-balance-check` is
  required.
- Taxable Income sections other than local and foreign dividend payments are not imported.
- If a newer All Trades report gives a non-zero brokerage charge in a currency different from the
  trade currency (GBP for foreign-exchange rows), the import stops. That fee cannot yet be
  represented exactly from this report without risking an incorrect allowable cost.

## Troubleshooting

### No transactions are detected

Check that you converted the spreadsheet worksheet to CSV: `.xlsx` files are not read. Confirm that
both files are directly inside the directory passed to `--sharesight-dir`, and that their filenames
start with `All Trades Report` and `Taxable Income Report`. Filenames are matched
case-insensitively; additional text after `Report` and uppercase `.CSV` extensions are accepted.

### `Missing expected columns`

cgt-calc found the report's header row but not every column it needs. This happens when headings
were edited during conversion, or when Sharesight has changed the report format.

First upgrade cgt-calc using the same method you used to install it and try again. If the error
remains, open a [GitHub issue](https://github.com/KapJI/capital-gains-calculator/issues/new) with:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- the CSV header and a sanitised failing row.

Do not upload an unredacted report: it can contain holdings and other sensitive financial
information.

### `Unknown action`

Compare the named action with [Known limitations](#known-limitations). Do not remove the row unless
you replace it with an equivalent supported transaction whose UK tax treatment you have verified.

### The portfolio or dividend totals look wrong

Check that the All Trades export starts at the portfolio's first transaction, the Taxable Income
export covers the tax year being calculated, and both came from the same GBP portfolio. Compare
dividends and deducted tax with your broker statements, and review the terminal's final portfolio
for missing or excessive holdings before relying on the tax report.
