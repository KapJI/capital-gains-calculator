# Charles Schwab

cgt-calc reads the CSV export of a Charles Schwab brokerage account. It treats every value in the
export as US dollars and converts it to pounds using HMRC's monthly exchange rate for the month of
each transaction.

If you receive shares from an employer, you may also need an Equity Awards CSV to supply the market
price used for each employer-share acquisition. Read [Equity awards](#equity-awards) before
exporting that file: Schwab currently produces more than one award layout, and cgt-calc does not
support all of them.

## Export the complete transaction history

Use the Schwab website rather than a positions export, statement, realised gain/loss report or PDF:

1. Open the transaction history for the taxable brokerage account.
2. Select the longest date range available. Start with the account's first transaction if possible,
    as explained in [Before you start](../usage.md#before-you-start). End today or at least 30 days
    after the end of the tax year you are calculating, because a purchase in the 30 days after a
    disposal can affect how that disposal is matched.
3. Include all transaction types. A trade-only export can omit deposits, dividends, tax, fees and
    corporate actions needed by the calculation.
4. Export the results as CSV.

The exact controls are behind the Schwab login and may change. Use the account's transaction-history
CSV whose columns include `Date`, `Action`, `Symbol`, `Description`, `Price`, `Quantity`,
`Fees & Comm` and `Amount`. You can compare it with the
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/schwab/data/schwab_transactions.csv).

Schwab may limit how much history can be exported at once. If your history needs several exports,
repeat the process with consecutive date ranges, making sure there are no gaps.

## Prepare the directory

One export goes to `--schwab-file`. Several go in a directory passed to `--schwab-dir`, so you do
not have to combine them yourself:

```text
schwab/
├── 2022-04-06_to_2023-04-05.csv
├── 2023-04-06_to_2024-04-05.csv
└── 2024-04-06_to_2025-04-05.csv
```

The base filenames do not matter. cgt-calc reads every CSV file directly inside the directory, but
it does not search subdirectories.

Two rules to get right:

- **Do not put the Equity Awards CSV in this directory.** It is also a `.csv`, so cgt-calc would
    read it as transaction history and stop with `Missing columns in Schwab transaction file`. Keep
    it elsewhere and pass it with `--schwab-award-file`.
- **Do not put exports from two different Schwab accounts in one directory.** The CSV does not say
    which account a row belongs to, so cgt-calc cannot separate them. Combining several accounts is
    not supported.

Overlaps are **not** safe here, unlike some other brokers. A Schwab CSV carries no transaction ID,
so cgt-calc cannot tell a row repeated by an overlap from a genuine repeat of the same trade: two
identical buys of the same size and price on one day are a real thing. If two exports have
overlapping transaction-date spans, cgt-calc refuses them rather than count both.

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --schwab-file schwab_transactions.csv
```

`--year 2025` means 6 April 2025 to 5 April 2026. Follow [Generate and Review a Report](../usage.md)
to find and check the output.

If your history needed several exports, point `--schwab-dir` at the directory holding them instead:

```shell
cgt-calc --year 2025 --schwab-dir schwab/
```

`--schwab-file` and `--schwab-dir` cannot be used together.

If a `Stock Plan Activity` row has no price, also pass the supported Equity Awards CSV described
below:

```shell
cgt-calc --year 2025 \
  --schwab-file schwab_transactions.csv \
  --schwab-award-file schwab_awards.csv
```

## Recognised activity

The importer recognises these exact values from the main CSV's `Action` column:

| Exported action                                                    | How cgt-calc handles it                                                             |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| `Buy`, `Sell`                                                      | A purchase or disposal, including `Fees & Comm`                                     |
| `Cancel Buy`                                                       | Removes the cancellation and the matching `Buy`                                     |
| `Stock Plan Activity`                                              | An employer-share acquisition at the exported or Equity Awards price; no cash moves |
| `Qualified Dividend`, `Cash Dividend`, `Qual Div Reinvest`         | Dividend income                                                                     |
| `Div Adjustment`, `Special Qual Div`, `Non-Qualified Div`          | Dividend income                                                                     |
| `NRA Tax Adj`, `NRA Withholding`, `Foreign Tax Paid`               | Tax deducted from a dividend                                                        |
| `Credit Interest`, `Bond Interest`                                 | Interest income                                                                     |
| `Short Term Cap Gain`, `Long Term Cap Gain`                        | A fund distribution reported with dividend income                                   |
| `ADR Mgmt Fee`                                                     | A charge added to the holding's pooled cost and deducted from cash                  |
| `Adjustment`, `IRS Withhold Adj`, `Wire Funds Adj`                 | A cash-balance correction                                                           |
| `MoneyLink Transfer`, `MoneyLink Deposit`, `MoneyLink Adj`         | A cash movement only                                                                |
| `Wire Funds`, `Wire Sent`, `Wire Funds Received`, `Funds Received` | A cash movement only                                                                |
| `Misc Cash Entry`, `Service Fee`, `Journal`, `Cash In Lieu`        | A cash movement only                                                                |
| `Visa Purchase`                                                    | A cash movement only                                                                |
| `Security Transfer`                                                | An `ACH`-related cash movement; an `Amount` is required and no holding is moved     |
| `Reinvest Shares`                                                  | A purchase of the reinvested shares                                                 |
| `Reinvest Dividend`                                                | Unsupported; the row is ignored and cgt-calc prints a warning                       |
| `Stock Split`                                                      | A share reorganisation: the pooled cost is unchanged and spread over the new count  |
| `Spin-off`                                                         | Adds the new holding and moves part of the old holding's pooled cost to it          |
| `Cash Merger` followed by `Cash Merger Adj`                        | A disposal for cash, built from the adjacent amount and quantity rows               |
| `Full Redemption Adj` followed by `Full Redemption`                | A disposal for cash, built from the adjacent amount and quantity rows               |
| `Sell to Open`                                                     | Writing an equity option: a disposal of the option on the grant date                |
| `Buy to Close`                                                     | Closing a written option: an allowable cost of the original grant                   |
| `Expired`                                                          | A written option that lapsed; the grant keeps the premium as its gain               |
| `Assigned`                                                         | A written option that was assigned; the premium moves into the share transaction    |

An `NRA Tax Adj`, `NRA Withholding` or `Foreign Tax Paid` row is treated as tax on account interest
only when its `Symbol` is blank and its `Description` contains `SCHWAB1 INT`. Otherwise, it is
treated as dividend tax.

For `Reinvest Dividend`, check the Schwab statement and the finished report. Confirm that the
dividend income and reinvested purchase were recorded by other rows; do not assume the ignored row
duplicates them.

cgt-calc changes Schwab's old `FB` ticker to `META`, so transactions under both names share one
holding in the report.

An unknown action stops the import. Do not delete a financial transaction simply to make the run
finish; identify what happened and check [Known limitations](#known-limitations) first.

### Cancelled purchases

Schwab keeps both a cancelled purchase and its `Cancel Buy` row in the export. cgt-calc removes both
when their symbol, quantity and price match and the purchase is no more than five days before the
cancellation. An unmatched cancellation stops the import because leaving either row in the
calculation could create a purchase that never happened.

### Dates containing `as of`

Schwab can show a date such as `08/18/2023 as of 08/15/2023`. cgt-calc uses the first date,
`08/18/2023`, for the transaction. An Equity Awards CSV can supply a market price from the earlier
date, but it does not change the transaction date.

That choice affects the tax year and the same-day and 30-day matching rules. Check any such row
carefully when the two dates cross 5 April or another purchase or disposal falls between them.

### Bonds and Treasury securities

When Schwab identifies a bond with a valid nine-character CUSIP, it may quote the price per $100 of
face value. cgt-calc converts that to a price per $1 and checks it against the quantity and total
amount. Accrued interest found through that check is included with the transaction costs.

If the total does not support the conversion, cgt-calc prints a warning and leaves the exported
price unchanged. Do not ignore that warning: compare the price, face value, cash amount and accrued
interest with the trade confirmation before relying on the result.

### Corporate actions

A `Stock Split` row states the change the event made to this account's holding: positive for the new
shares a split added, negative for the shares a consolidation removed. cgt-calc applies it as a
share reorganisation: nothing is bought or sold, and the pooled cost is unchanged and spread over
the new count ([TCGA 1992 s127](https://www.legislation.gov.uk/ukpga/1992/12/part/IV/chapter/II)).

Two situations are refused rather than guessed:

- If the same security also has units from another input, one account's change does not state the
    whole pooled holding's. Replace the Schwab row's effect with a RAW
    [`STOCK_SPLIT` row](raw.md#share-reorganisations) stating the change to all of them together;
    where a date has both, the RAW row is used and the Schwab row is ignored. See
    [`also has units from`](raw.md#also-has-units-from-on-a-stock_split-row).
- Because Schwab rows carry dates without times, a purchase, sale, gift or transfer of the same
    security on the day of the split cannot be placed before or after it, and the day is refused.
    See [`cannot be placed either side of it`](raw.md#cannot-be-placed-either-side-of-it).

For a `Spin-off`, cgt-calc needs to know the old holding from which the new shares came. It asks for
the old ticker during an interactive run and saves the answer in `out/spin_offs.csv`. For a
non-interactive run, add the required `dst,src` row first or choose another cache with
[`--spin-offs-file`](../extra-data-and-options.md#spin-off-source-mappings). It then uses the two
holdings' market values to divide the existing pooled cost. Check this result against the company's
reorganisation documents.

Cash mergers are supported only when the exported pair represents shares leaving in return for cash.
cgt-calc warns because a merger that also gives you replacement shares needs different treatment and
is not covered.

## Written equity options

The importer supports standard US equity options written with `Sell to Open`, including covered
calls. It reconciles these outcomes:

- **`Buy to Close`:** the closing debit is an allowable cost of the original option grant. The gain
    remains dated when the option was written.
- **`Expired`:** the premium, less the opening transaction costs, remains the gain on the grant.
- **`Assigned`:** a call premium is added to the proceeds from selling the underlying shares; a put
    premium reduces the acquisition cost of the shares bought on assignment. The usual share
    identification rules then apply.

This follows HMRC's guidance for investors in
[traded options (CG55536)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg55536),
[closing out a written option (CG55545)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg55545),
and
[exercise by the grantor (CG12313)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg12313).
It assumes the activity is investment activity taxed under the capital gains rules rather than a
trade.

Both Schwab option-symbol formats are accepted, for example `META 09/20/2024 500.00 C` and
`META  240920C00500000`. Support assumes a standard contract delivering 100 shares.

Include the complete history from each `Sell to Open` through its close, expiry or assignment, even
where that crosses 5 April. A later close changes the gain on the original grant; a later assignment
replaces that option gain with the combined share transaction. Re-run earlier tax years when a
previously open option reaches either outcome, and amend a submitted return if necessary.

Schwab posts the underlying share transaction of an assignment on its own settlement date, which is
a business day or two later and can fall in the next tax year. cgt-calc dates the share transaction
on the assignment instead, and takes its amounts from the exported settlement row. That row has to
be in the export: the shares are never invented from the assignment alone, because the settlement
row turning up later would then dispose of the same shares twice. The import stops, naming the
transaction it looked for, when it is missing, when more than one exported row could be it, when the
only candidate does not reconcile with the strike, quantity and fees, or when one contract is
assigned more than once on a day, which the export gives no way to pair up with its share rows.

The money keeps the settlement date. During import, cgt-calc represents the exported share row as a
cash movement on the date Schwab paid or collected the money. This prevents the assignment from
showing a shortfall before settlement.

Same-day ordering is unchanged. Schwab rows are read from the bottom up, so a funding deposit listed
above the share row is processed after it and the balance check can still fail.

If a balance-check error includes the generated cash movement, its description is
`Settlement of the META put option expiring 2024-05-17 at a strike of 50 assigned on 2024-05-17`.

Purchased options (`Buy to Open`, `Sell to Close`, `Exercised`) are not supported. Neither are
cash-settled index options, nor adjusted contracts, whose root carries a numeric suffix such as
`AAPL1` and which no longer deliver 100 shares. The parser stops with an explicit error for those
rows instead of treating contracts as shares. Box spreads are not recognised as financing: their
legs are read as ordinary written and purchased options, and the purchased legs stop the import.

## Equity awards

### Recommended CSV method

Use this method when the main transaction CSV contains `Stock Plan Activity` rows with a blank
`Price`:

1. In Schwab, open the Equity Awards account. Schwab's
    [grant guide](https://eac.schwab.com/content/how-to-accept-your-grant) shows how to reach the
    account from **Accounts → Equity Awards**.
2. Export its complete transaction history as CSV.
3. Check that the file's heading contains `Date`, `Symbol` and `FairMarketValuePrice`.
4. Pass it with `--schwab-award-file` alongside the main CSV.

The award-history export control is behind the Schwab login, so its wording may change. The file's
columns and row layout, rather than the button name, determine whether cgt-calc supports it.

This extra CSV does **not** import a second set of transactions. It supplies a missing market price
for a vest in the main CSV. cgt-calc looks for the same symbol on the activity date or one of the
previous six days, which covers awards dated around weekends and holidays.

The supported award layout stores one activity across two CSV rows. Keep the file unchanged. A
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/schwab/data/rsu_settlement/awards.csv)
shows the expected structure.

Some newer Equity Awards CSVs have columns such as `VestFairMarketValue` but no
`FairMarketValuePrice`. That layout is not yet supported. Renaming the column is not a safe
workaround because the rest of the row structure also differs.

### Legacy JSON method

cgt-calc also has a separate importer for older Schwab Equity Awards JSON exports:

```shell
cgt-calc --year 2025 --schwab-equity-award-json schwab_awards.json
```

This is a complete transaction importer, unlike `--schwab-award-file`. Use it only for an actual
Schwab JSON transaction export that you have checked. Do not import the same vest, sale, dividend or
cash movement again through `--schwab-file`.

The JSON importer supports restricted-stock vests, ESPP purchases, sales, forced quick sales,
dividends, dividend tax and forced cash disbursements. It recognises gifts but stops so that you can
classify the recipient, as explained below. It skips `Lapse` rows because they repeat the shares
already recorded by the related vest. For an ESPP purchase it uses the market value on the purchase
date as the acquisition cost, on the assumption that the discount was taxed as employment income.
Check that assumption against your payroll and award records.

#### Share splits in legacy JSON exports

Schwab has sometimes updated old award records inconsistently after a share split. The JSON importer
contains specific handling for Alphabet (`GOOG` and `GOOGL`) and NVIDIA (`NVDA`). For any other
ticker it prints a warning because it does not know how Schwab changed the old quantities.

After a new split, download the complete JSON history again and check every pre-split acquisition
and disposal quantity against a statement. A stale export can mix old and new units without changing
the cash totals, so the balance check cannot detect the error.

If cgt-calc warns that a disposal is cheaper by about the split multiplier, compare the named
disposal's quantity and proceeds with a statement. The warning can mean that Schwab already adjusted
the quantity for the split, but a real fall in the share price can look the same.

#### Gifts in a legacy JSON export

A JSON `Gift` row says that shares left the account but not who received them. cgt-calc therefore
stops and asks you to classify it:

- For a spouse or civil partner, follow
    [Transfers to a spouse or civil partner](raw.md#transfers-to-a-spouse-or-civil-partner) and add
    the suggested `TRANSFER_TO_SPOUSE` row through a small RAW file.
- For anyone else, follow [Gifts to anyone else](raw.md#gifts-to-anyone-else) and add the suggested
    `GIFT` or `GIFT_UNCONNECTED` row using a verified market value.

The error prints the complete RAW line to copy into a small CSV. The JSON importer can restate an
old gift for a later split, so the quantity may not match the number of shares shown on the gift
date. Where both readings are possible, the error prints two candidate lines: check a statement and
use the one with the correct quantity.

## Known limitations

- Transfers of shares are not reconstructed from the main CSV. In particular, `Journaled Shares` is
    unsupported. Despite its name, `Security Transfer` is supported only as an `ACH`-related cash
    movement: it requires an `Amount` and does not move shares. Follow the RAW transfer or gift
    instructions only after establishing what the transfer was and what cost should move with it.
- The newer Equity Awards CSV layout without `FairMarketValuePrice` is unsupported, as described
    above.
- A cash merger that gives replacement shares as well as cash is not supported. The cash-only
    importer prints a warning for every merger so you remember to check this.
- All values in Schwab CSV and JSON files are treated as USD. Changing a currency symbol in the CSV
    does not convert the data.
- cgt-calc does not remove duplicates from Schwab exports, because the CSV has no transaction ID to
    tell a duplicate from a genuine repeat of the same trade. Every transaction must appear exactly
    once. `--schwab-dir` refuses exports whose transaction-date spans overlap rather than count
    both.
- Exports from more than one Schwab account cannot be combined. Nothing in the CSV identifies the
    account.

## Troubleshooting

### `Cannot price a vest`

The named `Stock Plan Activity` has no price in the main CSV. Export the Equity Awards CSV and pass
it with `--schwab-award-file`. If you already did, check that it contains the same ticker and a
`FairMarketValuePrice` dated no more than six days before the activity.

If the file instead uses the newer `VestFairMarketValue` layout, it is not supported. Do not rename
columns or invent a price merely to bypass the error.

### `Missing columns` or a row/column-count error

Make sure `--schwab-file` points to the main transaction-history CSV and `--schwab-award-file`
points to the supported award-price CSV. A positions export, realised gain/loss report, statement,
JSON file or spreadsheet converted from PDF has a different layout.

With `--schwab-dir`, this error names the offending file: take it out of the directory (or, if it is
the Equity Awards CSV, pass it with `--schwab-award-file` instead).

If you combined several history ranges, confirm that there is one header, every data row has the
same number of fields, and none of the source files used a different export format.

### `is not a regular file` or `could not be read`

With `--schwab-dir`, every entry matching `*.csv` must be a plain, readable file. Remove or move out
a subdirectory, broken symlink, or file the current user lacks permission to read.

### `Unknown action`

Compare the action named in the error with [Recognised activity](#recognised-activity). Common
unsupported examples include `Journaled Shares`. Do not remove the row until you know whether it
represents a trade, transfer or corporate action that must be recorded another way.

First upgrade cgt-calc using the same method you used to install it. If an unchanged export still
contains an unsupported action, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the complete
error and a sanitised copy of the row.

### An unmatched `Cancel Buy`

Re-export without transaction filters and make sure the date range includes the original purchase.
The matching `Buy` must have the same ticker, quantity and price and be no more than five days
before the cancellation. Check the pair against Schwab before following the row-removal instruction
in the error.

### A cash merger or full redemption pair is rejected

Use the unchanged export. The two rows must be adjacent and have the same date, symbol and
description. One supplies the proceeds and the other the negative quantity. A changed order, extra
value or replacement shares means the event is not the supported cash-only shape.

### `The lapse on ... reports ... shares`

This legacy JSON error means the share count on a `Lapse` row disagrees with the related deposited
and withheld counts after applying the split history currently known to cgt-calc. Download a fresh
complete JSON export, then compare the named lapse, its vest and the split history with the original
award statement. cgt-calc stops rather than applying a split factor that may be wrong. If a fresh
export still fails, cgt-calc may not yet know about a recent split; open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the error and a
sanitised copy of the relevant records.

### `ESPP purchase on ... bought ... shares but accounts for ...`

This legacy JSON error means the purchased share count does not equal the shares deposited, withheld
and sold for tax after any known split. Compare those figures with the ESPP confirmation and split
history. Do not change a quantity merely to make the import continue.

### An export overlaps another one

Two files in the `--schwab-dir` directory have earliest-to-latest transaction-date spans that
overlap. They need not share any individual date: one file running from March to December and
another holding a single June transaction is enough, because cgt-calc cannot then rule out that the
two exports cover the same period. Because a Schwab CSV has no transaction ID, it cannot tell which
rows are repeats, so it refuses rather than count them twice. Re-export the ranges so they do not
overlap, or delete the redundant file.

If the two files are exports from different Schwab accounts, that is the cause: cgt-calc cannot
combine accounts, because no column says which account a row came from.

### `Reached a negative balance` or `Tried to sell not owned symbol`

Check that the export reaches the deposits and purchases that funded or created the later activity.
Also check for a missing award vest, unsupported share transfer, gap between downloaded ranges or a
transaction imported twice.

Do not add made-up cash or use `--no-balance-check` simply to silence the error. Use that option
only after you understand why the Schwab history cannot reconcile and have checked its completeness
another way.

## Check the finished report

Compare “Portfolio at the end of … tax year” with the Schwab account record for 5 April. Check each
disposal quantity and proceeds, every employer-share vest and market value, dividends and tax,
interest, bond face values, split-adjusted quantities and corporate-action warnings.

Do not upload an unredacted Schwab export to GitHub. It contains account activity, holdings, award
identifiers and other sensitive financial information.
