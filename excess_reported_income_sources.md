# Excess Reported Income specific formats and fund information

This guide is only needed in case the funds data you're looking for is not pre-bundled with the tool.
Currently bundled data:

-   [Vanguard Funds 2018-2024](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)
-   [Blackrock Funds 2019-2024](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/blackrock_eri.csv)
-   [iShares Funds 2018-2024](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/ishares_eri.csv)

<details>
    <summary>🏦 Instructions for ERI_RAW format</summary>

You will need:

-   **CSV using the ERI_RAW format.** This is currently the only format supported for excess reported income.
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

Vanguard Investment Series Plc reports are for traditional funds, Vanguard Funds Plc reports are for ETFs.

Note this tool **already includes** Vanguard Funds ERI data from 2018 to 2024.

To contribute new data to the tool please run the tool on your capital gains data from the git repository with the `--import-eri-reports` options pointing to either the file or the folder containing the ERI reports for Blackrock or iShares.
The tool will recognize the funds provider from the filename and import the data in the resource CSV for [vanguard](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv).

The tool also record any new ISIN translation to the resource CSV for [ISIN](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv)

Create a pull request with all the files in GitHub adjusting the README and this file with the updated bundled data.

</details>
 <br />
<details>
    <summary>🏦 Instructions for iShares/Blackrock funds</summary>

Blackrock UK publishes the Reportable Income yearly report at the bottom of this page:
https://www.blackrock.com/uk/solutions/adviser-resources/reporting-fund-status

They are split in Index Funds (BGIF), Global Funds (BGF), Strategic Funds (BSF)

iShares UK publishes the Reportable Income yearly reports at this link:
https://www.ishares.com/uk/individual/en/education/library?materialType=tax+information

They are split in different companies holding the funds each reporting yearly.

To contribute new data to the tool please run the tool on your capital gains data from the git repository with the `--import-eri-reports` options pointing to either the file or the folder containing the ERI reports for Blackrock or iShares.
The tool will recognize the funds provider from the filename and import the data in the resource CSV for [blackrock](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/blackrock_eri.csv) or [ishares](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/eri/ishares_eri.csv).

The tool also record any new ISIN translation to the resource CSV for [ISIN](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv)

Create a pull request with all the files in GitHub adjusting the README and this file with the updated bundled data.

</details>
 <br />
<details>
    <summary>🏦 Instructions for Xtrackers funds</summary>

DWS UK publishes the Reportable Income yearly report at the bottom of this page:
https://etf.dws.com/en-gb/information/etf-documents/reportings/

They are split XTrackers (stocks ETF), XTrackers II (bonds ETF) and XTrackers IE (other stocks ETF).

Columns mapping to ERI_RAW:

-   **ISIN:** same name column
-   **Fund Reporting Period End Date:** Period Ended date at the top of the PDF
-   **Currency:** Share class currency column
-   **Excess of reporting income over distribution:** Excess reported income per share column
</details>
 <br />
<details>
    <summary>🏦 Instructions for Amundi funds</summary>

Amundi UK publishes the Reportable Income yearly report at the bottom of this page:
https://www.amundietf.co.uk/en/individual/resources/document-library?documentType=uktaxcalculation

They are split XTrackers (stocks ETF), XTrackers II (bonds ETF) and XTrackers IE (other stocks ETF).

Columns mapping to ERI_RAW:

-   **ISIN:** same name column
-   **Fund Reporting Period End Date:** Reporting Period End Date column
-   **Currency:** Currency of the following amounts column
-   **Excess of reporting income over distribution:** Per unit excess reportable income over distributions in respect of the reporting period column
</details>
 <br />
