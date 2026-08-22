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

Use the `TRANSFER_TO_SPOUSE` action to record shares given to a spouse or civil partner where the
transfer qualifies for no gain / no loss treatment under
[TCGA 1992 s58](https://www.legislation.gov.uk/ukpga/1992/12/section/58): the shares leave your
Section 104 pool at their base cost with a nil gain, and the report shows the base cost that passes
to the recipient.

That normally means you were living together at some point in the tax year of the transfer. Since 6
April 2023 it can also cover transfers made after separating, up to the earlier of the end of the
third tax year after the year of separation or the date of the final order — see
[CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420). Check you qualify
before using this action; if you do not, the transfer is a disposal at market value and cgt-calc
cannot work it out yet.

Leave `price` at `0` — a gift has no consideration, so it is ignored. Any fee you paid to make the
transfer belongs in `fees`: it is an incidental cost of the disposal, so it is added to the base
cost passed to the recipient.

```csv
2024-03-16,TRANSFER_TO_SPOUSE,META,21.5,0.00,0.00,USD
```

No broker export marks these, so you have to add the row yourself. You do not have to convert your
whole history to the RAW format to do it: pass a RAW file holding just the transfers alongside your
broker export, and the two are merged.

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --raw-file transfers.csv
```

### How the shares are identified

The transfer is a disposal for identification purposes, so the same-day and 30-day rules apply to it
as they would to a sale
([CG22200](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22200),
[s106A](https://www.legislation.gov.uk/ukpga/1992/12/section/106A)). That only changes anything if
you bought the same shares on the transfer date or within the next 30 days, in which case the base
cost passing to the recipient is the cost of those shares rather than the pool average. Otherwise,
the shares simply leave the Section 104 pool at its average cost.

!!! warning "Selling and transferring on the same day"

    One case is refused: selling and transferring the same symbol on the same day when you also
    acquired it that day or in the next 30.
    [Section 105(1)](https://www.legislation.gov.uk/ukpga/1992/12/section/105) makes same-day
    disposals a single transaction, but one part is chargeable and the other is not, and nothing
    says how to split the matched acquisitions between them. Without such an acquisition both take
    the same pool average and it calculates normally.
