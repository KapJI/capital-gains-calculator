# Adding a Broker

1. Add a new parser class in
   [`cgt_calc/parsers/`](https://github.com/KapJI/capital-gains-calculator/tree/main/cgt_calc/parsers)
2. Register it in
   [`cgt_calc/parsers/broker_registry.py`](https://github.com/KapJI/capital-gains-calculator/blob/main/cgt_calc/parsers/broker_registry.py)
3. Add tests in `tests/`
4. Add a docs page under `docs/brokers/` and a nav entry in `zensical.toml`, and add the broker to
   the table in [Brokers](../brokers/index.md)
5. Submit a pull request describing your changes
