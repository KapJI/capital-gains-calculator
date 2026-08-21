# Sharesight

You will need:

- **Exported transaction history from Sharesight.** Sharesight is a portfolio tracking tool with
  support for multiple brokers.
  - You will need the "All Trades" and "Taxable Income" reports since the beginning. Make sure to
    select "Since Inception" for the period, and "Not Grouping".
  - Export both reports to Excel or Google Sheets, save as CSV, and place them in the same folder.
  - [See example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/sharesight/data/inputs).

Comments:

- Sharesight aggregates transactions from multiple brokers, but doesn't necessarily have balance
  information. Use the `--no-balance-check` flag to avoid spurious errors.

- Since there is no direct support for equity grants, add `Stock Activity` as part of the comment
  associated with any vesting transactions - making sure they have the grant price filled
  ([see example](https://github.com/KapJI/capital-gains-calculator/tree/main/tests/sharesight/data/inputs)).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --no-balance-check --sharesight-dir sharesight_trxs_dir/
```
