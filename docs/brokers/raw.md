# RAW Format

You will need:

- **CSV using the RAW format.** If your broker isn't natively supported you might choose to convert
  whatever report you can produce into this basic format.
  [See example](https://github.com/KapJI/capital-gains-calculator/blob/main/tests/raw/data/test_data_2.csv).
  Include the header row shown below (lower-case column names in this order). The parser can infer
  the column order when the header is missing, but it will emit a warning so you can update your
  export the next time.

  - `date` – transaction date in `YYYY-MM-DD` format.
  - `action` – one of the supported broker actions (see
    [`ActionType`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/model.py)).
  - `symbol` – instrument ticker; leave blank for cash movements if not applicable.
  - `quantity` – number of shares or units involved (blank for cash-only transactions).
  - `price` – price per unit in the transaction currency (blank when not applicable).
  - `fees` – fees associated with the transaction (blank or `0` if none).
  - `currency` – ISO currency code of the transaction amounts (for example `USD`).

Example usage for the tax year 2024/25:

```shell
cgt-calc --year 2024 --raw-file raw_data.csv
```

## Transfers to a spouse or civil partner

Shares given to a spouse or civil partner usually move at **no gain / no loss**: nothing is taxable
for you, and they inherit your base cost
([CG22200](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22200)). Record them with
the `TRANSFER_TO_SPOUSE` action.

```csv
2024-03-16,TRANSFER_TO_SPOUSE,META,21.5,0.00,0.00,USD
```

Leave `price` at `0`, since a gift has no sale price. Put any fee you paid to make the transfer in
`fees` — it is added to the base cost the recipient inherits.

No broker export marks these, so you add the row yourself. You do not have to convert your whole
history to the RAW format to do it: pass a small RAW file of just the transfers alongside your
broker export.

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --raw-file transfers.csv
```

The report and the PDF show the base cost that passes to the recipient, and the text report prints
the exact RAW row to give them.

### If you received the shares

Record them arriving with `TRANSFER_FROM_SPOUSE`, with the base cost per unit as the `price`. The
transferor's report prints the row ready to use. The shares enter your Section 104 pool at that
cost, acquired on the transfer date, and no money is added to your cash balance.

```csv
2024-03-16,TRANSFER_FROM_SPOUSE,META,21.5,95.60,0.00,GBP
```

Your broker may show the shares arriving, but it does not know what they cost, so leave that row out
of your export rather than let it be read as a purchase at nothing.

!!! warning "Check that you qualify"

    No gain / no loss applies if you were living together at some point in the tax year of the
    transfer, and since 6 April 2023 for a period after separating as well — see
    [CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420). If you do not
    qualify, or the shares went to anyone else, record a [gift](#gifts-to-anyone-else) instead.

Buying the same shares within 30 days of a transfer changes the figure. The transfer is matched
against that purchase in the same way a sale would be, so the recipient inherits the cost of those
shares rather than your pool average. Selling and transferring the same shares on the same day is
refused when there is such a purchase, because there is no rule for splitting it between the two;
the error says what to do instead.

## Gifts to anyone else

Shares given to anyone other than a spouse or civil partner are a **disposal at market value**
([TCGA 1992 s17](https://www.legislation.gov.uk/ukpga/1992/12/section/17)): you are taxed as if you
had sold them for what they were worth on the day, although no money changed hands. Record them with
the `GIFT` action, with the market value per share on that day as the `price`.

```csv
2024-03-16,GIFT,META,21.5,480.00,0.00,USD
```

The same identification rules as a sale apply, and a gain counts like any other.

!!! warning "A loss on a gift is reported on its own"

    Most people you would give shares to are connected persons
    ([s286](https://www.legislation.gov.uk/ukpga/1992/12/section/286)) — family, mainly — and a loss
    on a disposal to a connected person can only be set against gains on disposals to the same
    person ([s18(3)](https://www.legislation.gov.uk/ukpga/1992/12/section/18)). cgt-calc cannot
    know who is connected, so a loss on a gift is listed separately and left out of the loss total.
    If the recipient is not a connected person, add the loss to your losses yourself.

Gifts to charity are no gain / no loss
([s257](https://www.legislation.gov.uk/ukpga/1992/12/section/257)) and should not be recorded this
way.
