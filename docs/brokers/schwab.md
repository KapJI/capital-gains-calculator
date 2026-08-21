# Charles Schwab

You will need:

- **Exported transaction history in CSV format.** Schwab only allows you to download transactions
  for the last 4 years. If you require more, you can download the history in 4-year chunks and
  combine them.
  [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/schwab/data/schwab_transactions.csv).
- **Exported transaction history from Schwab Equity Awards in CSV format.** Only applicable if you
  receive equity awards in your account (e.g. for Alphabet/Google employees). Follow the same
  procedure as in the normal transaction history but selecting your Equity Award account.

Example usage for the tax year 2020/21:

```shell
cgt-calc --year 2020 --schwab-file schwab_transactions.csv --schwab-award-file schwab_awards.csv
```

_Note: For historic reasons, it is possible to provide the Equity Awards history in JSON format with
`--schwab-equity-award-json`. Instructions are available at the top of this
[parser file](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/parsers/schwab_equity_award_json.py).
Please use the CSV method above if possible._

## NVIDIA equity awards

The JSON parser handles NVDA as well as GOOG/GOOGL. Schwab reports NVDA history in mixed units:
which fields are restated for the 4:1 split of 20 July 2021 and the 10:1 of 10 June 2024 varies by
record type.

| Record                  | Read as                                                                                                   |
| ----------------------- | --------------------------------------------------------------------------------------------------------- |
| `Deposit` / `RS` — vest | `Quantity` and `VestFairMarketValue` as recorded; both are already restated and agree                     |
| `Deposit` / `ESPP`      | `NetSharesDeposited` when tax was settled in shares, else `Quantity`, priced at `PurchaseFairMarketValue` |
| `Lapse`                 | checked for consistency, then skipped                                                                     |
| `Sale`                  | multiplied by the split factor for the trade date, priced from `(Amount + fees) / Quantity`               |

Three of these change a number:

- **ESPP enters the pool at market value, not the price paid.** The discount is taxed as employment
  income through payroll. Using `PurchasePrice` understates the basis roughly tenfold on the older
  purchases.
- **A `Lapse` is not an acquisition.** It duplicates its `Deposit`, and its counts are in the units
  of their own day while its price is restated; pairing them pools a tenth of the shares at the full
  price.
- **Disposals need the split multiplier and acquisitions do not.** Missing it costs 54 shares across
  two 2023 disposals, and the pool stays wrong by that much every year after.

Getting the units wrong changes no amount of money, so nothing fails to balance and only the share
count moves. Two things are therefore checked and raise `ParsingError`: the split table against a
`Lapse`, which states both units at once so
`Quantity == (NetSharesDeposited +
SharesSoldWithheldForTaxes) × multiplier` has to hold; and that
an ESPP purchase adds up, deposited plus withheld plus sold against what was bought. A third only
warns — a disposal priced far below the acquisitions around it has usually been restated twice, but
a share that fell that far looks the same.

`SPLITS` gains a row when NVDA next splits. Schwab restates acquisitions retroactively, so a stale
export becomes a mix of units and has to be downloaded again in full.

Covered by
[`tests/schwab/test_schwab_equity_award_nvda.py`](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/schwab/test_schwab_equity_award_nvda.py),
against a synthetic portfolio with real dates and prices and invented share counts.

## Gifted shares

An Equity Awards export can contain a `Gift` action: shares that left the account with no money
attached. cgt-calc refuses to calculate until you say who received them, because that decides the
tax and the export does not record it:

- **A spouse or civil partner you live with.** No gain / no loss — see
  [transfers to a spouse](raw.md#transfers-to-a-spouse-or-civil-partner). Record the gift as a
  `TRANSFER_TO_SPOUSE` row in a small RAW file, using the same date, symbol and quantity, and pass
  it alongside your Schwab file. The error message prints the exact line to copy.
- **Anyone else.** A disposal at market value, which is usually chargeable. cgt-calc cannot work
  that out yet, so it has to be done by hand.

Once the RAW row is there, cgt-calc treats the broker's own gift row as accounted for and ignores
it, so the shares leave your pool once rather than twice.
