# Contributing ERI Data

Sharing compiled Excess Reported Income data saves significant time for other users holding the same
funds. Where each provider publishes its reports — and how their columns map to the ERI_RAW format —
is described in [Providing custom ERI data](../custom-eri-data.md#where-providers-publish-reports).

## Importing provider reports

For most providers you can import reports automatically. Run the
[`import_eri_reports.py`](https://github.com/KapJI/capital-gains-calculator/blob/main/scripts/import_eri_reports.py)
script pointing to either the file or the folder containing the downloaded ERI reports. The tool
recognizes the funds provider from the filename and imports the data into the matching resource CSV
under
[`cgt_calc/resources/eri/`](https://github.com/KapJI/capital-gains-calculator/tree/main/cgt_calc/resources/eri).
Vanguard, BlackRock, iShares, Xtrackers and Invesco reports are all supported this way. The script
also records any new ISIN translations into
[`initial_isin_translation.csv`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv).

Amundi reports have no automatic importer — add their data manually as described below.

## Manual additions and ISIN translations

To add data by hand, append the rows at the bottom of the provider's resource CSV under
[`cgt_calc/resources/eri/`](https://github.com/KapJI/capital-gains-calculator/tree/main/cgt_calc/resources/eri).
Then run the tool once to generate any new ISIN translations (if needed) and copy them from your own
ISIN translation file (default `out/isin_translation.csv`) into
[`initial_isin_translation.csv`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/resources/initial_isin_translation.csv).

## Pull request checklist

- Updated resource CSV(s) under `cgt_calc/resources/eri/`
- Any new rows in `cgt_calc/resources/initial_isin_translation.csv`
- Updated bundled-data list in [Offshore funds (ERI)](../offshore-funds.md#bundled-data)
