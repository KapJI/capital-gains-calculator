# Providing Custom ERI Data

This is only needed when the fund data you're looking for is not pre-bundled with the tool. Check
the [bundled data](offshore-funds.md#bundled-data) first.

## ERI_RAW format

- **CSV using the ERI_RAW format.** This is currently the only format supported for excess reported
    income.
    [See example.](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --eri-raw-file eri_raw.csv [broker_transactions_options...]
```

## Where providers publish reports

Each provider publishes an annual Reportable Income report. The tables below map each provider's
columns to the ERI_RAW format so you can compile your own file.

### Vanguard

Vanguard UK publishes the Reportable Income yearly report at the bottom of this page:
<https://www.vanguardinvestor.co.uk/investing-explained/general-account-tax-information>

Vanguard Investment Series Plc reports are for traditional funds, Vanguard Funds Plc reports are for
ETFs.

Note this tool **already includes** Vanguard Funds ERI data from 2018 to 2025.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** Share Class Currency column
- **Excess of reporting income over distribution:** same name column

### iShares / BlackRock

Blackrock UK publishes the Reportable Income yearly report at the bottom of this page:
<https://www.blackrock.com/uk/solutions/adviser-resources/reporting-fund-status>

They are split in Index Funds (BGIF), Global Funds (BGF), Strategic Funds (BSF)

iShares UK publishes the Reportable Income yearly reports at this link:
<https://www.ishares.com/uk/individual/en/education/library?materialType=tax+information>

They are split in different companies holding the funds each reporting yearly.

- **ISIN:** same name column
- **Fund Reporting Period End Date:** End date in the Reporting Period column
- **Currency:** same name column
- **Excess of reporting income over distribution:** Excess of reporting income per unit column

### Xtrackers

DWS UK publishes the Reportable Income yearly report at the bottom of this page:
<https://etf.dws.com/en-gb/information/etf-documents/reportings/>

They are split XTrackers (stocks ETF), XTrackers II (bonds ETF) and XTrackers IE (other stocks ETF).

Columns mapping to ERI_RAW:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Period Ended date at the top of the PDF
- **Currency:** Share class currency column
- **Excess of reporting income over distribution:** Excess reported income per share column

### Amundi

Amundi UK publishes the Reportable Income yearly report at the bottom of this page:
<https://www.amundietf.co.uk/en/individual/resources/document-library?documentType=uktaxcalculation>

Columns mapping to ERI_RAW:

- **ISIN:** same name column
- **Fund Reporting Period End Date:** Reporting Period End Date column
- **Currency:** Currency of the following amounts column
- **Excess of reporting income over distribution:** Per unit excess reportable income over
    distributions in respect of the reporting period column

### Invesco

Invesco publishes the Reportable Income yearly report in the documents section of any fund with UK
reporting status:
<https://www.invesco.com/uk/en/financial-products/etfs/invesco-uk-gilts-ucits-etf-acc.html#Documents>

Note this tool **already includes** Invesco Funds ERI data from 2018 to 2024.

Columns mapping to ERI_RAW:

- **ISIN:** ISIN / Identifier column
- **Fund Reporting Period End Date:** Stated in the report header before the main table
- **Currency:** Currency of Share Class column
- **Excess of reporting income over distribution:** Per unit excess reportable income over
    distributions in respect of the reporting period column

## Contributing data back

If you compile ERI data for any fund, please contribute it so other holders of the same fund can
reuse it — see [Contributing ERI data](development/eri-data.md).
