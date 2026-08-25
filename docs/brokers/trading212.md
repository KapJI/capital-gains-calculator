# Trading 212

cgt-calc reads the CSV account history exported by Trading 212. Give it a directory rather than a
single file because a complete account history may require several exports.

Export the Invest account only. Do not include ISA activity: income and capital gains from
[investments in an ISA do not need to be declared](https://www.gov.uk/individual-savings-accounts/how-isas-work).
If you also export the ISA for your own records, keep it in a separate directory. The CSV does not
identify the account type, so cgt-calc cannot separate the accounts later.

## Export your account history

1. In Trading 212, open **Menu**, then **History**.
2. Select the export button.
3. Choose the date range. Exporting the history since the account was opened is the safest option;
   see [Before you start](../usage.md#before-you-start) for why earlier transactions may be needed.
4. Select every available data category so that orders, cash transactions, dividends and interest
   are included.
5. Download the CSV file.
6. If your complete history requires multiple exports, repeat the process with consecutive date
   ranges, making sure there are no gaps.

The official
[Trading 212 export instructions](https://helpcentre.trading212.com/hc/en-us/articles/360016898917-Can-I-export-the-trading-data-from-my-account)
show the current controls. Use the account-history export, not a PDF statement or the separate pie
export.

## Prepare the directory

Put the CSV files directly in one directory, for example:

```text
trading212/
├── from_2022-04-06_to_2023-04-05.csv
├── from_2023-04-06_to_2024-04-05.csv
└── from_2024-04-06_to_2025-04-05.csv
```

The base filenames do not matter. cgt-calc reads every CSV file directly inside the directory, but
it does not search subdirectories. Do not add unrelated CSV files, and make sure there are no gaps
between date ranges. Overlaps are safe: a transaction that two exports both list is kept once.

You can compare the structure with this
[sanitised example export](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/trading212/data/2024/inputs/transactions.csv).

## Generate the report

For the 2024/25 tax year, run:

```shell
cgt-calc --year 2024 --trading212-dir trading212/
```

`--year 2024` means 6 April 2024 to 5 April 2025. Follow [Generate Your First Report](../usage.md)
to find and check the output.

## Supported activity

The Trading 212 parser currently handles:

| Activity          | Included transactions                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Orders            | Market, limit, stop and stop-limit buys and sells                                                                                                                  |
| Income            | Ordinary, manufactured and property income dividends; dividend adjustments; cash, lending and fund interest                                                        |
| Cash activity     | Deposits, withdrawals, card credits, card debits, card refunds, currency conversions, result adjustments and cashback adjustments                                  |
| Corporate actions | Transactions labelled `Stock Split` or `Spin off`                                                                                                                  |
| Costs and taxes   | Transaction, regulatory and currency-conversion fees; stamp duty, stamp duty reserve tax and French transaction tax, including costs charged in a foreign currency |

### Known limitations

- Dividends are recorded at the CSV `Total`; the `Withholding tax` column is not used and does not
  appear separately in the report.
- Share transfers between accounts or brokers, labelled `Transfer in` or `Transfer out`, are not
  supported.
- Split transactions labelled `Stock split open` or `Stock split close` are not supported. Only the
  single-row `Stock Split` action is recognised.
- The
  [export for a Trading 212 contract for difference account](https://helpcentre.trading212.com/hc/en-us/articles/36243765206301-How-to-export-the-trading-data-from-my-CFD-account)
  uses a different, record-based CSV format that this parser does not support.

Do not delete an unsupported transaction from the export to make the calculation run. The missing
activity could make the resulting holdings and gains incorrect.

## Troubleshooting

### `Unknown column(s)` or `Unknown action`

Trading 212 occasionally changes its export format. First, upgrade cgt-calc using the same method
you used to install it and try again. If the error remains, open a
[GitHub issue](https://github.com/KapJI/capital-gains-calculator/issues/new) containing:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message;
- the CSV header for an unknown column; or
- a sanitised failing row for an unknown action.

Do not upload an unredacted account export: it can contain transaction IDs and other financial
information.

### No transactions are detected

Check that the directory contains CSV files directly, rather than inside another directory. The file
extension is matched case-insensitively, so `.csv`, `.CSV` and mixed-case variants all work. Also
check that you passed the directory itself to `--trading212-dir`, not the path to one CSV file.

### The balance or portfolio looks wrong

Re-export the history with all data categories selected. Check for a missing date range, files from
the wrong account, or an unsupported action listed above.

### A price-per-share warning appears

Review transactions where the price multiplied by the quantity, after currency conversion and fees,
does not match the total shown in the CSV. cgt-calc continues after this warning, so resolve the
discrepancy against the transaction in Trading 212 before relying on the report.
