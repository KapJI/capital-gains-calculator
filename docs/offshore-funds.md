# Offshore Funds (ERI)

For correct taxation on
[offshore funds](https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds)
you need to specify the yearly excess reported income (ERI) from each fund you have owned. You can
find the full list of funds that requires this at
[HMRC](https://www.gov.uk/government/publications/offshore-funds-list-of-reporting-funds).

The tool already includes such yearly history in the
[resources folder](https://github.com/cgt-calc/capital-gains-calculator/tree/main/cgt_calc/resources/eri).
You can check if your fund is already included, or provide a custom ERI history file following the
instructions in [Providing custom ERI data](custom-eri-data.md). We strongly suggest sharing
compiled ERI data so it can be added to the package as it can save significant time to other users
that hold the same fund.

## Bundled data

Currently bundled data:

- [Vanguard Funds Plc 2018-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)
- [Blackrock Funds 2019-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/blackrock_eri.csv)
- [iShares Funds 2018-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/ishares_eri.csv)
- [Invesco Funds 2018-2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/invesco_eri.csv)
- [Xtrackers Funds 2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/xtrackers_eri.csv)

The ERI funds are indexed by ISIN and the tool provides automatic translation from ISIN to tickers,
in case your broker doesn't supply the ISIN in their transaction history. For instructions on how to
override ISIN translation see the **ISIN to ticker translation** entry on the
[Configuration files](configuration.md) page.

## Unsupported functionality

There are a few **unsupported** functionalities at the moment for taxation on offshore funds:

- Tax calculations for offshore funds that are **not reporting to HMRC** as they don't report taxes
  as CGT but as income tax.
- Excess Reported Income
  [equalisation](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13224) support which
  is an optional arrangement which certain funds can support to reduce the amount of excess reported
  income in case you held the fund stocks for less than the reporting period.

## Providing custom ERI data

If your fund isn't in the bundled data above, you can compile a custom ERI history file. See
[Providing custom ERI data](custom-eri-data.md) for the ERI_RAW format and where each provider
publishes its reports.
