# Interactive Brokers (IBKR)

You will need:

- **Exported transaction history from Interactive Brokers.** From web, go to **Performance & Reports
  → Transaction History**. Select a period since creation of the account and click the export CSV
  icon.
  [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/interactive_brokers/data/test_basic.csv).

Example usage for the tax year 2025/26:

```shell
cgt-calc --year 2025 --interactive-brokers-file U000000-TRANSACTIONS.csv
```
