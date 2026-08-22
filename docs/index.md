# UK Capital Gains Calculator

Prepare **UK tax figures** from your investment transaction history and generate a detailed
calculation report. cgt-calc is intended for UK individual investors working from supported broker
exports.

Supported sources include **Charles Schwab**, **Freetrade**, **Hargreaves Lansdown**, **Interactive
Brokers**, **Morgan Stanley**, **Sharesight**, **Trading 212**, **Vanguard**, or a custom **RAW**
format.

For supported transactions, the tool converts prices to **GBP** and applies the UK **same-day**,
**30-day (“bed and breakfast”)**, and **Section 104 holding** rules. It summarizes disposal
proceeds, allowable costs, gains, losses, dividends, and interest in the terminal and a detailed
**PDF report**.

cgt-calc does **not** calculate your final tax bill or submit a tax return. Some investment
scenarios are not supported; check the relevant [broker guide](brokers/index.md) and the
[offshore funds limitations](offshore-funds.md#unsupported-functionality) before relying on the
result.

The PDF report includes separate **Capital Gains** and **Interest and Dividends** sections, with a
summary at the end. Interest is grouped **monthly per broker** to keep reports concise, even for
brokers that pay daily interest.

## Example report

Here's what a generated PDF report looks like:

[![Preview of example report](assets/example_report_preview.png){ width="600" }](assets/example_report.pdf)

[View full example report (PDF)](assets/example_report.pdf)

## Where to start

- [Installation](installation.md) — install the tool and LaTeX
- [Usage](usage.md) — generate your first report
- [Brokers](brokers/index.md) — export instructions for each supported broker
- [Offshore funds (ERI)](offshore-funds.md) — if you hold non-UK funds
- [Development](development/index.md) — contribute to the project
