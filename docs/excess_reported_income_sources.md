# Excess Reported Income specific formats and fund information

This guide is only needed in case the funds data you're looking for is not pre-bundled with the
tool. Currently bundled data:

- [Vanguard Funds Plc 2018-2024](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)

<details>
    <summary>🏦 Instructions for ERI_RAW format</summary>

You will need:

- **CSV using the ERI_RAW format.** This is currently the only format supported for excess reported
  income.
  [See example.](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --eri-raw-file eri_raw.csv [broker_transactions_options...]
```

</details>
 <br />
<details>
    <summary>🏦 Instructions for Vanguard funds</summary>

Vanguard UK publishes the Reportable Income yearly report at the bottom of this page:
https://www.vanguardinvestor.co.uk/investing-explained/general-account-tax-information

Vanguard Investment Series Plc reports are for traditional funds, Vanguard Funds Plc reports are for
ETFs.

Note this tool **already includes** Vanguard Funds Plc ERI data from 2018 to 2024.

Columns mapping to ERI_RAW:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** Share Class Currency column
- **Excess of reporting income over distribution:** same name column

</details>
 <br />
<details>
    <summary>🏦 Instructions for Blackrock (iShares) funds</summary>

Blackrock UK publishes the Reportable Income yearly report at the bottom of this page:
https://www.blackrock.com/uk/solutions/adviser-resources/reporting-fund-status

They are split in Index Funds (BGIF), Global Funds (BGF), Strategic Funds (BSF)

Columns mapping to ERI_RAW:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** same name column
- **Excess of reporting income over distribution:** Excess of reporting income per unit column

</details>
 <br />
<details>
    <summary>🏦 Instructions for Xtrackers funds</summary>

DWS UK publishes the Reportable Income yearly report at the bottom of this page:
https://etf.dws.com/en-gb/information/etf-documents/reportings/

They are split XTrackers (stocks ETF), XTrackers II (bonds ETF) and XTrackers IE (other stocks ETF).

Columns mapping to ERI_RAW:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Period Ended date at the top of the PDF
- **Currency:** Share class currency column
- **Excess of reporting income over distribution:** Excess reported income per share column

</details>
 <br />
<details>
    <summary>🏦 Instructions for Amundi funds</summary>

Amundi UK publishes the Reportable Income yearly report at the bottom of this page:
https://www.amundietf.co.uk/en/individual/resources/document-library?documentType=uktaxcalculation

They are split XTrackers (stocks ETF), XTrackers II (bonds ETF) and XTrackers IE (other stocks ETF).

Columns mapping to ERI_RAW:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Reporting Period End Date column
- **Currency:** Currency of the following amounts column
- **Excess of reporting income over distribution:** Per unit excess reportable income over
  distributions in respect of the reporting period column

</details>
 <br />
