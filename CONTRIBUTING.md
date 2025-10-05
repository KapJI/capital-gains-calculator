# Contributing to cgt-calc

Thanks for your interest in improving **cgt-calc** — contributions of all kinds are welcome!

## 🧑‍💻 Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management, testing, and builds.

### 1. Install uv

Follow [uv’s installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the repository

```bash
git clone https://github.com/KapJI/capital-gains-calculator.git
cd capital-gains-calculator
```

### 3. Set up the environment

```bash
uv sync
```

This sets up the development environment with all dependencies installed.

## 🧱 Code style

All checks in CI must pass before merging changes.

We use:

-   [ruff](https://docs.astral.sh/ruff/) — for linting and formatting
-   [pylint](https://pylint.readthedocs.io/en/stable/) — for additional linting
-   [prettier](https://prettier.io/) — for Markdown, YAML, and JSON formatting
-   [mypy](https://mypy-lang.org/) — for static type checking
-   [pytest](https://docs.pytest.org/) — for running tests

`pre-commit` can be used to run all checks with one command (see below).

## 🚸 Pre-commit

Install [`pre-commit`](https://pre-commit.com/#install) first, e.g. using `uv` or `pipx`:

```bash
uv tool install pre-commit
```

Installing it globally avoids issues when `pre-commit` invokes `uv` inside hooks.

Activate the `pre-commit` hook:

```bash
pre-commit install
```

This will automatically check code style, linting, and types before each commit.

You can also run all checks on the repository manually:

```bash
pre-commit run --all-files
```

Or you can run single hook:

```bash
pre-commit run mypy --all-files
pre-commit run pytest
```

## 🧹 Running linters and tests manually

You can also run linters and tests directly:

```bash
uv run pytest
uv run pytest -k <expr> -q # run subset
uv run ruff check .
uv run mypy cgt_calc
```

## 🧩 Adding Support for a New Broker

1. Add a new parser in `cgt_calc/parsers/`
2. Add tests in `tests/`
3. Update documentation and examples
4. Submit a pull request describing your changes

## 🏗️ Release Process (maintainers only)

Releases are created from draft GitHub releases created by [Release Drafter](https://github.com/release-drafter/release-drafter).
When a release is published, GitHub Actions will automatically:

1. Extract the version from the release tag (e.g. `v1.2.3`)
2. Run tests and checks
3. Build and publish the package to PyPI using `uv publish` and [Trusted Publisher](https://docs.pypi.org/trusted-publishers/) on PyPI
