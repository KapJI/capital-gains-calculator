# Trading 212

You will need:

- **Exported transaction history from Trading 212.** You can provide a folder containing several
  files since Trading 212 limit the statements to 1 year periods.
  [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/trading212/data/2024/inputs).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --trading212-dir trading212_trxs_dir/
```
