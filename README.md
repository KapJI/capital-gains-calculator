[![CI](https://github.com/KapJI/capital-gains-calculator/actions/workflows/ci.yml/badge.svg)](https://github.com/KapJI/capital-gains-calculator/actions)
[![codecov](https://codecov.io/gh/KapJI/capital-gains-calculator/graph/badge.svg)](https://app.codecov.io/gh/KapJI/capital-gains-calculator)
[![PyPI version](https://img.shields.io/pypi/v/cgt-calc)](https://pypi.org/project/cgt-calc/)

# <img src="https://raw.githubusercontent.com/KapJI/capital-gains-calculator/main/docs/assets/favicon.png" alt="" width="34" valign="middle"> UK Capital Gains Calculator

Easily calculate **UK Capital Gains Tax** from your investment transaction history.

Supported sources include **Charles Schwab**, **Trading 212**, **Morgan Stanley**, **Sharesight**,
**Hargreaves Lansdown**, **Vanguard**, **Freetrade**, **Interactive Brokers**, or a custom **RAW**
format.

The tool generates a detailed **PDF report** with all calculations.

All prices are automatically converted to **GBP**, and all **HMRC rules** are applied — including
the **same-day**, **bed and breakfast**, and **Section 104 holding** rules.

📖 **Full documentation: [cgt-calc.uk](https://cgt-calc.uk/)**

## 📊 Example Report

<a href="https://github.com/KapJI/capital-gains-calculator/blob/main/docs/assets/example_report.pdf">
  <img src="https://raw.githubusercontent.com/KapJI/capital-gains-calculator/main/docs/assets/example_report_preview.png" alt="Preview of example report" width="600">
</a>

## 🚀 Quick Start

Install with [uv](https://docs.astral.sh/uv/concepts/tools/#the-uv-tool-interface) (or pipx/pip) and
generate a report for a tax year:

```shell
uv tool install cgt-calc
cgt-calc --year 2024 --schwab-file schwab_transactions.csv
```

`pdflatex` must be on your `PATH` to generate the PDF report. See
[Installation](https://cgt-calc.uk/installation/) and [Usage](https://cgt-calc.uk/usage/) for
details, and [Brokers](https://cgt-calc.uk/brokers/) for how to export your transaction history.

## 📚 Documentation

- [Installation](https://cgt-calc.uk/installation/)
- [Usage](https://cgt-calc.uk/usage/)
- [Broker instructions](https://cgt-calc.uk/brokers/)
- [Offshore funds (ERI)](https://cgt-calc.uk/offshore-funds/)
- [Configuration files](https://cgt-calc.uk/configuration/)
- [Docker](https://cgt-calc.uk/docker/)
- [Privacy & disclaimer](https://cgt-calc.uk/privacy/)

## 🤝 Contributing

Contributions are welcome! If you find a bug, have feature ideas, or want to add support for more
brokers, please open an **issue** or **pull request**. See the
[development guide](https://cgt-calc.uk/development/).

## ⚠️ Disclaimer

Please note: I'm **not a tax adviser**. Use this tool and its outputs **at your own risk**.
