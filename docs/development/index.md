# Contributing

Thanks for your interest in improving **cgt-calc** — contributions of all kinds are welcome! If you
find a bug or have feature ideas, please open an
[issue](https://github.com/cgt-calc/capital-gains-calculator/issues) or pull request.

## Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management, testing, and builds.

### 1. Install uv

Follow [uv’s installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```shell
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the repository

```shell
git clone https://github.com/cgt-calc/capital-gains-calculator.git
cd capital-gains-calculator
```

### 3. Set up the environment

```shell
uv sync
```

This command creates a virtual environment and installs all project and development dependencies
into it. Run it again after pulling new changes to update dependencies.

## Code style

All checks in CI must pass before merging changes.

We use:

- [ruff](https://docs.astral.sh/ruff/) — for Python linting and formatting
- [mypy](https://mypy-lang.org/) — for static type checking
- [ty](https://docs.astral.sh/ty/) — for additional static type checking
- [pytest](https://docs.pytest.org/) — for running tests
- [dprint](https://dprint.dev/) — for formatting Markdown, YAML, TOML, JSON, and Dockerfiles
- [shfmt](https://github.com/mvdan/sh#shfmt) - for formatting shell scripts
- [codespell](https://github.com/codespell-project/codespell) - for catching common misspellings
- [Harper](https://writewithharper.com/) - for grammar and spell checking of comments, docstrings,
  and Markdown docs

`prek` can be used to run all checks with one command (see below).

Links in Markdown are checked separately by [lychee](https://lychee.cli.rs/), which runs in CI. To
check them locally, install lychee and run `lychee .` from the repository root; its settings live in
`lychee.toml`.

The project uses **Python 3.12** as the minimum supported version.

## Prek

`prek` is fully compatible with `pre-commit`, so `pre-commit` can be used as well.

Install [`prek`](https://prek.j178.dev/) first, e.g. using `uv` or `pipx`:

```shell
uv tool install prek
```

Installing it globally avoids issues when `prek` invokes `uv` inside hooks.

Activate the `prek` hook:

```shell
prek install
```

This will automatically check code style, linting, and types before each commit.

Type suppressions must name the diagnostic for both checkers. When both tools report the same
intentional violation, keep their targeted comments together:

```python
value = untyped_value  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
```

You can also run all checks on the repository manually:

```shell
prek run --all-files
```

Or you can run single hook:

```shell
prek run mypy --all-files
prek run ty --all-files
prek run pytest
prek run --hook-stage manual python-typing-update --all-files
```

Harper runs as a regular hook via `uv` (the `harper-cli` dev dependency), so no separate
installation is needed. Project vocabulary (tickers, broker names, identifiers) lives in
`.harper-dictionary.txt`; the Harper editor extension picks it up automatically. Words that the
dictionary cannot whitelist — Harper dialect-gates US spellings such as product names — are accepted
via regexes in `.harper-ignore.txt`.

## Running linters and tests manually

You can also run linters and tests directly:

```shell
uv run pytest
uv run pytest -k <expr> -q # run subset
uv run ruff check .
uv run mypy cgt_calc tests scripts
uv run ty check cgt_calc tests scripts
```

## Managing dependencies

You can manage dependencies either with `uv` commands or by editing `pyproject.toml` directly.

### Add a new runtime dependency

```shell
uv add <package-name>
```

### Add a development dependency

```shell
uv add --group dev <package-name>
```

### Upgrade existing dependencies

```shell
uv lock --upgrade
uv sync
```

### Manual changes

If you edit `pyproject.toml` manually (for example, to bump a version), run `uv sync` afterwards to
apply the changes and update `uv.lock`.

## Updating the example report

To regenerate the example PDF report used in the docs, run:

```shell
./scripts/generate_example_report.sh
```

This writes `docs/assets/example_report.pdf`. Commit the updated file if your changes affect report
generation. The example source transactions and fixed exchange rates live in
`scripts/example_data/`, so generation is deterministic and does not call the exchange-rate API.

After regenerating the PDF, update the images made from it: the teaser the README shows and the
full-page images the docs show:

```shell
./scripts/generate_example_preview.sh
```
