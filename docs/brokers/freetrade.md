# Freetrade

You will need:

- **Exported transaction history from Freetrade.** Go to **Activity → GIA → Last 12 Months → Export
  CSV**. The exported file may cover a longer period — this is useful, as it can include purchase
  prices.
  [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/freetrade/data/transactions.csv).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --freetrade-file freetrade_GIA.csv
```
