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

Use the `TRANSFER_TO_SPOUSE` action to record shares given to a spouse or civil partner you live
with. These are treated as no gain / no loss (TCGA 1992 s58): the shares leave your Section 104 pool
at their base cost with a nil gain, and the report shows the base cost that passes to the recipient.
Set `price` and `fees` to `0`.

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

A no gain / no loss transfer is still a **disposal**.
[Section 58](https://www.legislation.gov.uk/ukpga/1992/12/section/58) does not exempt it; it fixes
the consideration at "such amount as would secure that on the disposal neither a gain nor a loss
would accrue" to the transferor. That amount is the transferor's allowable cost, and for shares the
allowable cost is whatever the identification rules say it is — so the identification rules have to
be applied before s58 can be.

HMRC's own manual treats it as a disposal made by the transferor, when the transfer happens.
[CG22200](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22200) describes the
recipient's position entirely in terms of that disposal: their cost is "the deemed consideration on
the disposal by the transferor", and their acquisition date is "the date of disposal of the
transferor". Neither sentence works unless there was a disposal, made by the person giving the
shares away, on the day they gave them.

So the shares given away are identified under the normal rules, and a purchase of the same shares on
the transfer date or within the following 30 days is matched first and passes its own cost to the
recipient instead of the pool average.
[Section 106A](https://www.legislation.gov.uk/ukpga/1992/12/section/106A) applies "where _any_
securities are disposed of by _any_ person" and excludes only non-residents, and
[CG51560](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560) lists no exception
for transfers between spouses.

This only changes the answer when such a purchase exists. Without one the matching falls through to
the Section 104 pool, and the transfer is simply a reduction of the pool at its average cost.

!!! note "Not to be confused with selling and having your spouse buy back"

    Matching does not apply when you **sell** shares and your spouse **buys** them, because s106A
    matches a disposal only against securities reacquired by "the person making" it, and that is
    someone else. That is a different arrangement from the one here, where _you_ transfer shares and
    then _you_ buy the same shares back.

You can sell and transfer the same symbol on the same day as long as both come out of the Section
104 pool, since they then take the same average cost.

!!! warning "Selling and transferring against the same acquisitions"

    Selling and transferring the same symbol on the **same day** is rejected when you also acquired
    it that day or within the following 30 days.
    [Section 105(1)](https://www.legislation.gov.uk/ukpga/1992/12/section/105) treats everything
    disposed of on one day as a single transaction, but here one part is chargeable and the other is
    no gain / no loss, so they cannot be pooled, and nothing says how to split the matched
    acquisitions between them. That case has to be worked out manually. Always record the real
    transaction dates — do not alter them to work around this.
