# Usage

- You need to provide the **transaction history** for each of your accounts. See the
  [broker instructions](brokers/index.md).
- The history should include **all transactions** since you first acquired any shares owned during
  the relevant tax years.
- If you own or have owned **funds from outside the UK** (e.g. Ireland), whether accumulating or
  distributing, see the [Offshore funds](offshore-funds.md).
- Once you've gathered all transactions from all your brokers, generate a report — for example, for
  tax year 2020/21:

```shell
cgt-calc --year 2020 --schwab-file schwab_transactions.csv --trading212-dir trading212/ --mssb-dir mssb_report/
```

- Run `cgt-calc --help` for all available options.
- If your broker is not listed, try using the **RAW** format. Contributions for new brokers are very
  welcome!

## Terminal output

The report is written to **stdout** and all progress messages go to **stderr**, so the text report
can be piped or saved cleanly:

```shell
cgt-calc --year 2024 --schwab-file transactions.csv > report.txt
```

Colours and emoji are used automatically when the terminal supports them. The standard
[`NO_COLOR`](https://no-color.org/) and `FORCE_COLOR` conventions are respected, plus `NO_EMOJI` to
keep colours but drop emoji. Use `--verbose` for debug-level detail.
