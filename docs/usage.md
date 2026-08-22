# Generate Your First Report

## Before you start

You will need:

- **cgt-calc and LaTeX installed.** Follow the [installation guide](installation.md) before
  continuing.
- **A complete transaction history from every relevant account.** Follow the instructions for each
  [supported broker](brokers/index.md). Include transactions from before the tax year when they are
  needed to establish the cost of shares you owned or sold during that year. Exporting the history
  since the account was opened is the safest option.
- **Excess Reported Income data when applicable.** If you own or have owned funds from outside the
  UK, whether accumulating or distributing, check the [offshore funds guide](offshore-funds.md).

Do not include the same transaction in more than one export. cgt-calc can validate the transactions
it receives, but it cannot detect every missing or duplicated export.

## Choose the tax year

Pass the first year of the UK tax year to `--year`. For example, `--year 2024` means the 2024/25 tax
year, from 6 April 2024 to 5 April 2025.

If you omit `--year`, cgt-calc uses the most recently completed UK tax year.

## Generate the report

Pass the export using the option for your broker. For example, with a Charles Schwab export:

```shell
cgt-calc --year 2024 --schwab-file schwab_transactions.csv
```

You can combine inputs from different brokers in one calculation:

```shell
cgt-calc --year 2024 \
  --schwab-file schwab_transactions.csv \
  --trading212-dir trading212/ \
  --mssb-dir morgan_stanley/
```

See the [broker instructions](brokers/index.md) for the correct option and export format for each
source. If your broker is not listed, you can convert its transactions to the generic
[RAW format](brokers/raw.md).

## Find the results

cgt-calc prints a summary to the terminal and, by default, writes the detailed PDF report to
`out/calculations.pdf`.

Use `--output` to choose a different PDF location:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --output reports/2024-25.pdf
```

The summary is written to **stdout** and progress messages and warnings are written to **stderr**.
This means you can save the text summary without mixing in progress messages:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv > report.txt
```

Redirecting stdout does not change where the PDF is saved.

## Check the result

Before relying on the figures:

1. Read every warning printed while the calculator runs.
2. Check that the section headed “Portfolio at the end of … tax year” agrees with your records at
   the end of that tax year.
3. Compare the disposal count and proceeds with your broker statements.
4. Check that dividends and interest are present when you expect them.
5. Confirm that every relevant account was included once, without overlapping exports.
6. Check that unusual events such as share splits, spin-offs and offshore fund income were handled.

Avoid `--no-balance-check` unless the relevant broker guide recommends it or you understand why the
check cannot succeed. Disabling it removes one of the checks that can reveal incomplete transaction
history.

A successful run means cgt-calc could parse and calculate the supplied transactions. It does not
prove that the supplied history was complete or that every part of your tax position is supported.

Run `cgt-calc --help` for the complete list of available options. Use `--verbose` when you need more
detail while investigating a warning or error.

## Terminal appearance

Colours and emoji are used automatically when the terminal supports them. The standard
[`NO_COLOR`](https://no-color.org/) and `FORCE_COLOR` conventions are respected, plus `NO_EMOJI` to
keep colours but drop emoji.
