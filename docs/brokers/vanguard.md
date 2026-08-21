# Vanguard

You will need:

- **Exported transaction history from Vanguard.** Vanguard can generate a report in Excel format by
  going to "Documents → Report generator → Client Transaction Listing Excel". Vanguard will generate
  an Excel file with all transactions across all periods of time and all accounts (ISA, GA, etc).
  Export the tab you need (normally GIA account) as a CSV file.
  [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/vanguard/data/cash_investment_report.csv).

_Note_: In the exported CSV there will be two separate tables: a "Cash Transactions" table and
"Investment Transactions" table. The parser will automatically detect which is which by the header
names and join both. While you can manually save only the "Cash Transactions" and calculate capital
gain, some investment transactions are not shown in the "Cash Transactions" table. (For example, if
you set up selling of some assets to cover fees, the name of the asset and the amount of disposal
are not shown in the cash table). **Therefore, for accurate reporting it is recommended to use the
full CSV exported from the Excel file tab.**

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --vanguard-file vanguard.csv
```
