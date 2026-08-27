# Revolut

cgt-calc reads the CSV account statement exported by Revolut Invest. One export covers the whole
history of the account, so a single file is enough; cgt-calc takes one file rather than a directory.

Export the General Investment account only. Do not include ISA activity: income and capital gains
from
[investments in an ISA do not need to be declared](https://www.gov.uk/individual-savings-accounts/how-isas-work).
The CSV does not identify the account type, so cgt-calc cannot separate the accounts later.

## Export your account statement

1. In Revolut, open **Invest**.
2. Select **More**, then **Documents**.
3. Choose **General Investment**, then **Account statement**.
4. Choose the **CSV** format rather than PDF.
5. Set the Period to **All time**.
6. Generate the CSV file.

Exporting the statement for all time is the safest option; see
[Before you start](../usage.md#before-you-start) for why transactions from earlier tax years may be
needed.

Keep the exported columns unchanged.

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --revolut-file revolut.csv
```

The filename does not matter. `--year 2025` means 6 April 2025 to 5 April 2026. Follow
[Generate Your First Report](../usage.md) to find and check the output.

## Supported activity

The importer recognises these literal values from the CSV's `Type` column:

| Type                                       | How cgt-calc handles it                                                        |
| ------------------------------------------ | ------------------------------------------------------------------------------ |
| `BUY - MARKET`, `BUY - LIMIT`              | Share acquisitions                                                             |
| `SELL - MARKET`, `SELL - LIMIT`            | Share disposals                                                                |
| `DIVIDEND`                                 | Dividend income after withholding tax                                          |
| `DIVIDEND TAX (CORRECTION)`                | Corrections to dividend withholding tax                                        |
| `STOCK SPLIT`                              | The extra shares are pooled at zero cost, and the ratio adjusts share matching |
| `CUSTODY FEE`                              | Cash leaving the broker balance                                                |
| `CASH TOP-UP`, `CASH WITHDRAWAL`           | Cash added to or removed from the broker balance                               |
| `TRANSFER FROM REVOLUT ... TO REVOLUT ...` | Ignored: the move between Revolut entities is not a disposal or acquisition    |

The `Price per share` and `Total Amount` columns each carry a currency prefix, such as `USD 134.50`,
which must match the `Currency` column of the same row. cgt-calc recalculates the price as
`Total Amount` divided by `Quantity` instead of using the rounded price in the CSV, so the cost or
proceeds always agree with the total Revolut recorded. The statement has no separate fee column, so
anything Revolut already included in the total, rather than charging as a separate `CUSTODY FEE`
row, is part of the cost or proceeds.

The `FX Rate` column is not used. cgt-calc converts foreign currency amounts using the
[monthly exchange rates it downloads](../extra-data-and-options.md#exchange-rates), not the rate
shown by Revolut.

A `STOCK SPLIT` row states the number of shares added, not the new total holding, and its
`Total Amount` is zero.

### Dates and time zones

Revolut timestamps every transaction in UTC. cgt-calc converts each one to UK time, GMT in winter
and BST in summer, before taking the date. The tax year boundary and the same-day and 30-day
matching rules all run on UK calendar days, and the boundary always falls inside BST, so a
transaction stamped after 23:00 UTC on 5 April belongs to the following tax year.

## Known limitations

- Only the types listed above are mapped. Any other value stops the import with `Unknown action`; do
    not delete a financial transaction merely to make the calculation run, because the missing
    activity could make the resulting holdings and gains incorrect.
- Revolut reports dividend amounts after withholding tax, but does not export the tax amount.
- A custody fee reduces the broker balance but is not treated as an allowable cost against a gain.
- The statement covers the investment account only. Commodities and interest on savings products are
    not part of this export.

## Troubleshooting

### `Unknown action`

Revolut might add a new transaction type. First, upgrade cgt-calc using the same method you used to
install it and try again. If the error remains, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) containing:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- a sanitised copy of the failing row.

Do not upload an unredacted account statement: it contains your holdings and other financial
information.

### `Expected column ... but found ...` or `doesn't have 8 columns`

Make sure the file is an unchanged CSV account statement rather than a PDF converted to a
spreadsheet, and that the eight columns are still `Date`, `Ticker`, `Type`, `Quantity`,
`Price per share`, `Total Amount`, `Currency` and `FX Rate`, in that order.

### `missing header row`

cgt-calc warns when the first row does not look like the column names, and then assumes the columns
are in the standard order. Re-export the statement rather than relying on the guess, because a file
whose columns were reordered or removed would be read incorrectly.

### `Reached a negative balance`

Check that the period was set to **All time** so that the top ups, transfers and sales that funded
later purchases are all present. Do not add a made-up top up or use `--no-balance-check` just to
silence the error; establish the missing cash or holdings from your Revolut records first.

### The portfolio or dividends look wrong

Check the terminal section headed “Portfolio at the end of … tax year” against your Revolut holdings
on 5 April, and compare dividends and withholding with the statement. If a real exported row
disagrees with the report, open a GitHub issue with sanitised values from that row.
