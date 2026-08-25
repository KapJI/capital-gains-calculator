# Brokers

You need to provide the transaction history for each of your accounts, covering all transactions
since you first acquired any shares owned during the relevant tax years. Pick your broker below for
export instructions.

| Broker                                        | CLI option                             | Notes                               |
| --------------------------------------------- | -------------------------------------- | ----------------------------------- |
| [Charles Schwab](schwab.md)                   | `--schwab-file`, `--schwab-award-file` | Equity awards supported             |
| [Freetrade](freetrade.md)                     | `--freetrade-file`                     | Activity CSV export                 |
| [Hargreaves Lansdown](hargreaves-lansdown.md) | `--hl-dir`                             | Tax Centre CSVs plus contract notes |
| [Interactive Brokers](interactive-brokers.md) | `--interactive-brokers-file`           | Transaction history CSV             |
| [Morgan Stanley](morgan-stanley.md)           | `--mssb-dir`                           | Alphabet equity-award report folder |
| [Sharesight](sharesight.md)                   | `--sharesight-dir`                     | Multi-broker portfolio tracker      |
| [Trading 212](trading212.md)                  | `--trading212-dir`                     | Folder of yearly exports            |
| [Vanguard](vanguard.md)                       | `--vanguard-file`                      | Client Transactions Listing CSV     |
| [RAW format](raw.md)                          | `--raw-file`                           | Generic fallback for other brokers  |

If your broker is not listed, try the [RAW format](raw.md). Contributions for new brokers are very
welcome — see [Adding a broker](../development/adding-a-broker.md).
