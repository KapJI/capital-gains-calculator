# Revolut

cgt-calc reads the CSV account statement exported by Revolut Invest. One export covers the whole
history of the account, so a single file is enough; cgt-calc takes one file rather than a directory.

Export the General Investment account only. Do not include ISA activity: income and capital gains
from
[investments in an ISA do not need to be declared](https://www.gov.uk/individual-savings-accounts/how-isas-work).
The CSV does not identify the account type, so cgt-calc cannot separate the accounts later.

## Export your account statement

Follow the
[Revolut account statement instructions](https://help.revolut.com/help/wealth/stocks/getting-started-with-trading/managing-your-trading-account/trading-statements/accessing-my-trading-statements-and-reports/):

1. In Revolut, open **Invest**.
2. Select **More**, then **Documents**.
3. Choose **Stocks**, select your **General Investment** account, then **Account statement**.
4. Choose the **CSV** format rather than PDF.
5. Set the Period to **All time**.
6. Generate the CSV file.

Exporting the statement for all time is the safest option; see
[Before you start](../usage.md#before-you-start) for why transactions from earlier tax years may be
needed.

Keep the exported columns unchanged.

### Check the investments in the export

Check each holding's product type in Revolut. The CSV does not identify whether a ticker is a
company share, fund, bond or another product. cgt-calc treats every `BUY` and `SELL` row as a share
transaction and cannot detect when another tax treatment is needed.

- For an ETF or other fund, check whether it is an offshore fund and follow the
    [offshore-fund guide](../offshore-funds.md).
- cgt-calc does not identify or validate the tax treatment of bonds, ETNs, ETCs or other
    instruments. Calculate them outside cgt-calc.

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --revolut-file revolut.csv
```

The filename does not matter. `--year 2025` means 6 April 2025 to 5 April 2026. Follow the
[report guide](../usage.md) to find and check the output.

## Supported activity

The importer recognises these exact values from the CSV's `Type` column:

| Type                             | How cgt-calc handles it                                              |
| -------------------------------- | -------------------------------------------------------------------- |
| `BUY - MARKET`, `BUY - LIMIT`    | Share acquisitions                                                   |
| `SELL - MARKET`, `SELL - LIMIT`  | Share disposals                                                      |
| `DIVIDEND`                       | Net dividend income; see [Dividends](#dividends-and-withholding-tax) |
| `DIVIDEND TAX (CORRECTION)`      | Corrections to dividend withholding tax                              |
| `STOCK SPLIT`                    | Changes the share count without changing the existing pooled cost    |
| `CUSTODY FEE`                    | Cash leaving the broker balance                                      |
| `CASH TOP-UP`, `CASH WITHDRAWAL` | Cash added to or removed from the broker balance                     |

cgt-calc also ignores these two exact `Type` values because moving cash or holdings between Revolut
entities is not a disposal or acquisition:

- `TRANSFER FROM REVOLUT BANK UAB TO REVOLUT SECURITIES EUROPE UAB`
- `TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB`

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
`Total Amount` is zero. cgt-calc treats it as a share reorganisation: nothing is bought or sold, and
the pooled cost is unchanged and spread over the new share count
([HMRC CG51805](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51805)). The ratio
also adjusts share matching.

### Dates and time zones

Revolut timestamps every transaction in UTC. cgt-calc converts each one to UK time, GMT in winter
and BST in summer, before taking the date. The tax year boundary and the same-day and 30-day
matching rules all run on UK calendar days, and the boundary always falls inside BST, so a
transaction stamped at or after 23:00 UTC on 5 April belongs to the following tax year.

## Known limitations

- Only the types listed above are mapped. Any other value stops the import with `Unknown action`; do
    not delete a financial transaction merely to make the calculation run, because the missing
    activity could make the resulting holdings and gains incorrect.
- A Revolut `STOCK SPLIT` row states only the change in that account. cgt-calc stops if you also
    hold the ticker through another input. It also stops if Revolut booked another transaction for
    the ticker earlier on the same day, because the export does not say whether that transaction's
    share count is before or after the split. Follow the error's instructions to provide a complete
    [`STOCK_SPLIT` row](raw.md#share-reorganisations), or calculate the event outside cgt-calc.
- A custody fee reduces the broker balance but is not treated as an allowable cost against a gain.
- The statement covers the investment account only. Commodities and interest on savings products are
    not part of this export.

### Dividends and withholding tax

The `DIVIDEND` amount in the Revolut CSV is after withholding tax, but the CSV does not state the
gross dividend or the original tax deducted. cgt-calc reports the net amount as **Proceeds**. HMRC
calculates foreign income before direct foreign tax is deducted, so this figure is too low
([HMRC guidance](https://www.gov.uk/hmrc-internal-manuals/international-manual/intm165030)). A
`DIVIDEND TAX (CORRECTION)` row records only a later correction.

For each dividend, open **Invest**, then **Portfolio** → **Transactions** → **Dividend**. Revolut
shows the withholding tax in the transaction details
([Revolut dividend guide](https://help.revolut.com/help/wealth/stocks/corporate-events/receiving-dividends/)).
Use those details to establish the gross dividend and tax withheld, then adjust the dividend and
foreign-tax figures outside cgt-calc.

## Troubleshooting

### `Unknown action`

Revolut might add a new transaction type. First, upgrade cgt-calc using the same method you used to
install it and try again. If the error remains, open a GitHub issue containing:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message; and
- a sanitised copy of the failing row.

Do not upload an unredacted account statement: it contains your holdings and other financial
information.

### `CSV header mismatch`

The message names the columns that are missing and any it did not expect. Make sure the file is an
unchanged CSV account statement rather than a PDF converted to a spreadsheet, and that the eight
columns are still `Date`, `Ticker`, `Type`, `Quantity`, `Price per share`, `Total Amount`,
`Currency` and `FX Rate`. Their order does not matter, as each row is read by column name.

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
on 5 April. Compare each net dividend with the CSV and check its gross amount and withholding tax as
described above. If a real exported row disagrees with the report, open a GitHub issue with
sanitised values from that row.
