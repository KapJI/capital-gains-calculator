# Offshore Funds (ERI)

Not every non-UK fund is an offshore fund for UK tax purposes. Use HMRC's
[offshore funds guidance](https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds)
to confirm whether the rules apply.

For each offshore fund, check whether the exact share class had reporting fund status for the
periods you held it. Match the ISIN against HMRC's
[list of approved offshore reporting funds](https://www.gov.uk/government/publications/approved-offshore-reporting-funds),
not the fund name alone. Ask the fund manager if its status is unclear.

For a reporting fund, cgt-calc needs the excess reported income (ERI) per unit for each relevant
reporting period. This can apply to both accumulating and distributing share classes.

cgt-calc reports ERI as dividend income by default. Check HMRC's
[income classification](https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds).
If the holding is in a bond fund, pass its ticker to `--interest-fund-tickers` so cgt-calc reports
the ERI as interest. cgt-calc can classify ERI only as dividends or interest. If HMRC treats the
fund's income as property, miscellaneous or a mixture of income types, calculate the income outside
cgt-calc and do not use its income totals.

## Bundled data

cgt-calc currently bundles:

- [Vanguard Funds Plc 2018-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/vanguard_eri.csv)
- [Blackrock Funds 2019-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/blackrock_eri.csv)
- [iShares Funds 2018-2025](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/ishares_eri.csv)
- [Invesco Funds 2018-2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/invesco_eri.csv)
- [Xtrackers Funds 2024](https://github.com/cgt-calc/capital-gains-calculator/blob/main/cgt_calc/resources/eri/xtrackers_eri.csv)

Check that the bundled file contains the exact ISIN and every reporting period you need. cgt-calc
does not know when a reporting fund should have an ERI row. If a row is missing, check the fund's
report and use the [custom ERI guide](custom-eri-data.md) to add it. If supplied ERI cannot be
matched to the holding, check the ISIN-to-ticker mapping below. Do not assume either case means zero
ERI.

Bundled ERI data is indexed by ISIN. If your broker does not supply one, cgt-calc uses its
ISIN-to-ticker mapping to match the holding. To check or override the mapping, see **ISIN to ticker
translation** on the [Configuration files](configuration.md) page.

## Unsupported functionality

cgt-calc does not support:

- Disposing of a holding in an offshore fund that was non-reporting at any time while you owned it.
    A gain may be an offshore income gain subject to Income Tax, while a loss may still be an
    allowable capital loss. HMRC explains the
    [gain and loss treatment](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13410)
    and
    [funds that changed status](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13412).
    cgt-calc does not detect reporting status, elections or exceptions and applies ordinary CGT
    rules. Work out the disposal outside cgt-calc before using the report totals.
- Full equalisation adjustments. Taxable fund income (cash distributions and/or ERI) and the
    acquisition cost used for CGT can be reduced. cgt-calc does not make these adjustments; use the
    fund's report and HMRC's
    [income guidance](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13328) and
    [CGT guidance](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm13372) to make both
    adjustments outside the tool.

## Providing custom ERI data

If the bundled data does not contain the share class and reporting periods you need, compile a
custom ERI history file. See [Providing custom ERI data](custom-eri-data.md) for the ERI_RAW format
and where each provider publishes its reports.
