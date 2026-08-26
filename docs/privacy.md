# Privacy & Disclaimer

## Privacy and data security

**Your financial data stays on your computer.** This tool processes all transactions locally and
does **not send your transaction history or personal information** to any external services.

The only external API calls made are:

- [**UK Trade Tariff API**](https://www.trade-tariff.service.gov.uk/exchange_rates) – to fetch
  monthly GBP exchange rates (no personal data sent)
- [**Open FIGI API**](https://www.openfigi.com/api/overview) – to translate ISIN codes to tickers
  when needed (only ISIN codes are queried, no transaction amounts or personal details)

## Disclaimer

This tool is a calculation aid, **not tax advice**. It calculates capital gains figures from the
transaction history you provide and is designed to apply the UK tax rules it supports, following the
legislation and published HMRC guidance. Its authors and contributors are not acting as your tax
advisers, the results may contain errors, and tax rules change.

You are responsible for providing complete data and checking the figures against your records and
current HMRC guidance before using them in a tax return. For unusual or uncertain circumstances,
consider consulting a suitably qualified tax adviser. The software is provided "as is", without
warranty of any kind; see the
[MIT License](https://github.com/cgt-calc/capital-gains-calculator/blob/main/LICENSE).
