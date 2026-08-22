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
