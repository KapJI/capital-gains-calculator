[![PyPI version](https://img.shields.io/pypi/v/cgt-calc)](https://pypi.org/project/cgt-calc/)
[![CI](https://github.com/cgt-calc/capital-gains-calculator/actions/workflows/ci.yml/badge.svg)](https://github.com/cgt-calc/capital-gains-calculator/actions)
[![codecov](https://codecov.io/gh/cgt-calc/capital-gains-calculator/graph/badge.svg)](https://app.codecov.io/gh/cgt-calc/capital-gains-calculator)

# <img src="https://cgt-calc.uk/assets/logo.svg" alt="" width="34" valign="middle"> UK Capital Gains Calculator

Calculate your **UK capital gains** from your investment transaction history and generate a detailed
calculation report. cgt-calc is intended for UK individual investors working from supported broker
exports.

Supported sources include **Charles Schwab**, **Freetrade**, **Hargreaves Lansdown**, **Interactive
Brokers**, **Morgan Stanley**, **Sharesight**, **Trading 212**, **Vanguard**, or a custom **RAW**
format.

For supported transactions, the tool converts prices to **GBP** and applies the UK **same-day**,
**30-day ("bed and breakfast")**, and **Section 104 holding** rules. It prints a summary of disposal
proceeds, allowable costs, gains, losses, dividends, and interest to the terminal and writes the
full calculations to a **PDF report**.

cgt-calc reports the net gain from the transactions you supply and, for supported tax years,
estimates the amount remaining after the annual exempt amount. It does **not** account for gains or
losses outside those inputs, apply tax rates, work out your final tax bill, or submit a tax return.
Some investment scenarios are not supported; check the relevant
[broker guide](https://cgt-calc.uk/brokers/) and the
[offshore funds limitations](https://cgt-calc.uk/offshore-funds/#unsupported-functionality) before
relying on the result.

## 📊 Example Report

This compact 2025/26 example shows foreign-currency transactions, same-day and 30-day matching,
Section 104 pooling, gains and losses, a dividend with overseas tax, and cash interest.

<a href="https://cgt-calc.uk/assets/example_report.pdf">
  <img src="https://cgt-calc.uk/assets/example_report_preview.webp" alt="Preview of the 2025/26 example report" width="600">
</a>

## 🚀 Quick Start

Install with [uv](https://docs.astral.sh/uv/concepts/tools/#the-uv-tool-interface) (or pipx/pip) and
generate a report for a tax year:

```shell
uv tool install cgt-calc
cgt-calc --year 2025 --schwab-file schwab_transactions.csv
```

`pdflatex` must be on your `PATH` to generate the PDF report. See
[Installation](https://cgt-calc.uk/installation/) and [Usage](https://cgt-calc.uk/usage/) for
details, and [Brokers](https://cgt-calc.uk/brokers/) for how to export your transaction history.

## 📚 Documentation

Full documentation — installation, per-broker export guides, offshore funds (ERI), configuration,
and Docker usage — lives at **[cgt-calc.uk](https://cgt-calc.uk/)**.

## 🤝 Contributing

Contributions are welcome! If you find a bug, have feature ideas, or want to add support for more
brokers, please open an **issue** or **pull request**. See the
[development guide](https://cgt-calc.uk/development/).

## ⚠️ Disclaimer

This tool is a calculation aid, **not tax advice**. It calculates capital gains figures from the
transaction history you provide and is designed to apply the UK tax rules it supports, following the
legislation and published HMRC guidance. Its authors and contributors are not acting as your tax
advisers, the results may contain errors, and tax rules change.

You are responsible for providing complete data and checking the figures against your records and
current HMRC guidance before using them in a tax return. For unusual or uncertain circumstances,
consider consulting a suitably qualified tax adviser. The software is provided "as is", without
warranty of any kind; see the [MIT License](LICENSE).
