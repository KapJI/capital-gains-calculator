# Contributing to cgt-calc

Thanks for your interest in improving **cgt-calc** — contributions of all kinds are welcome!

## 🧑‍💻 Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management, testing, and builds.

### 1. Install uv

Follow [uv’s installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```shell
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the repository

```shell
git clone https://github.com/KapJI/capital-gains-calculator.git
cd capital-gains-calculator
```

### 3. Set up the environment

```shell
uv sync
```

This command creates a virtual environment and installs all project and development dependencies
into it. Run it again after pulling new changes to update dependencies.

## 🧱 Code style

All checks in CI must pass before merging changes.

We use:

- [ruff](https://docs.astral.sh/ruff/) — for Python linting and formatting
- [pylint](https://pylint.readthedocs.io/en/stable/) — for additional linting
- [mypy](https://mypy-lang.org/) — for static type checking
- [pytest](https://docs.pytest.org/) — for running tests
- [dprint](https://dprint.dev/) — for formatting Markdown, YAML, TOML, JSON, and Dockerfiles
- [shfmt](https://github.com/mvdan/sh#shfmt) - for formatting shell scripts
- [markdown-link-check](https://github.com/tcort/markdown-link-check) - for checking links in
  Markdown

`pre-commit` can be used to run all checks with one command (see below).

The project uses **Python 3.12** as the minimum supported version

## 🚸 Pre-commit

Install [`pre-commit`](https://pre-commit.com/#install) first, e.g. using `uv` or `pipx`:

```shell
uv tool install pre-commit
```

Installing it globally avoids issues when `pre-commit` invokes `uv` inside hooks.

Activate the `pre-commit` hook:

```shell
pre-commit install
```

This will automatically check code style, linting, and types before each commit.

You can also run all checks on the repository manually:

```shell
pre-commit run --all-files
```

Or you can run single hook:

```shell
pre-commit run mypy --all-files
pre-commit run pytest
pre-commit run --hook-stage manual python-typing-update --all-files
```

## 🧹 Running linters and tests manually

You can also run linters and tests directly:

```shell
uv run pytest
uv run pytest -k <expr> -q # run subset
uv run ruff check .
uv run mypy cgt_calc
```

## 🧩 Adding support for a new broker

1. Add a new parser in `cgt_calc/parsers/`
2. Add tests in `tests/`
3. Update documentation and examples
4. Submit a pull request describing your changes

## 📦 Managing dependencies

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

## 🧾 Updating the example report

To regenerate the example PDF report used in the docs, run:

```shell
./scripts/generate_example_report.sh
```

Commit the updated file if your changes affect report generation.

## 🏗️ Release process (maintainers only)

Releases are created from draft GitHub releases created by
[Release Drafter](https://github.com/release-drafter/release-drafter). When a release is published,
GitHub Actions will automatically:

1. Extract the version from the release tag (e.g. `v1.2.3`)
2. Run tests and checks
3. Build and publish the package to PyPI using `uv publish` and
   [Trusted Publisher](https://docs.pypi.org/trusted-publishers/) on PyPI
