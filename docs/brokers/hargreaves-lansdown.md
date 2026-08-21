# Hargreaves Lansdown

You will need:

- **Exported transaction history from Hargreaves Lansdown.** Hargreaves Lansdown can generate a tax
  report going to "Accounts → Tax Centre". HL will generate the report for all transactions across
  the selected period.
  - For simplicity you can generate reports for each tax year.
  - For each report you will need the export in CSV with the link provided by HL at the bottom of
    the report and all PDF contract notes for each buy and sell transaction which are linked
    directly for each transaction in the report shown.
  - Put the CSVs for all periods and their related PDF contract notes in the same folder. The report
    will automatically aggregate all transactions across all periods and generate a single report
    for the selected tax year.
  - [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/hl/data/inputs).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --hl-dir hl_data_dir/
```
