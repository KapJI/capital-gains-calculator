# Morgan Stanley

You will need:

- **Exported transaction history from Morgan Stanley.** Since Morgan Stanley generates multiple
  files in a single report, please specify a directory produced from the report download page.
  [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/morgan_stanley/data).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --mssb-dir morgan_stanley_trxs_dir/
```
