# UK Capital Gains Calculator

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
Some investment scenarios are not supported; check the relevant [broker guide](brokers/index.md) and
the [offshore funds limitations](offshore-funds.md#unsupported-functionality) before relying on the
result.

The PDF report includes separate **Capital Gains** and **Interest and Dividends** sections, with a
summary at the end. Interest is grouped **monthly per broker** to keep reports concise, even for
brokers that pay daily interest.

## Example report

This compact 2025/26 example shows foreign-currency transactions, same-day and 30-day matching,
Section 104 pooling, gains and losses, a dividend with overseas tax, and cash interest:

<div class="report-preview">
  <img src="assets/example_report_page1.webp" alt="Example report, page 1" loading="lazy">
  <img src="assets/example_report_page2.webp" alt="Example report, page 2" loading="lazy">
  <img src="assets/example_report_page3.webp" alt="Example report, page 3" loading="lazy">
</div>

[View full example report (PDF)](assets/example_report.pdf)

## Where to start

- [Installation](installation.md) — install the tool and LaTeX
- [Usage](usage.md) — generate and review a report
- [Brokers](brokers/index.md) — export instructions for each supported broker
- [Offshore funds (ERI)](offshore-funds.md) — if you hold non-UK funds
- [Development](development/index.md) — contribute to the project
