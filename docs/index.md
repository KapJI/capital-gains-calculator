# UK Capital Gains Calculator

Easily calculate **UK Capital Gains Tax** from your investment transaction history.

Supported sources include **Charles Schwab**, **Freetrade**, **Hargreaves Lansdown**, **Interactive
Brokers**, **Morgan Stanley**, **Sharesight**, **Trading 212**, **Vanguard**, or a custom **RAW**
format.

The tool generates a detailed **PDF report** with all calculations.

All prices are automatically converted to **GBP**, and all **HMRC rules** are applied — including
the **same-day**, **bed and breakfast**, and **Section 104 holding** rules.

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
