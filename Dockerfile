# syntax=docker/dockerfile:1.7

FROM python:3.9-slim-trixie AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    # Poetry's configuration:
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_CACHE_DIR='/var/cache/pypoetry' \
    POETRY_HOME='/usr/local'

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash ca-certificates curl texlive-latex-base \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Poetry (cached with BuildKit)
RUN --mount=type=cache,target=/root/.cache \
    curl -sSL https://install.python-poetry.org | python3 -

# 1) deps layer – copy only manifests first for better cache hits
COPY pyproject.toml poetry.lock* /build/
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/var/cache/pypoetry \
    poetry install --no-ansi --no-root

# 2) now copy sources and install package
COPY . /build
RUN poetry build -n \
 && poetry install --no-ansi

# Simple CLI shim
RUN printf '%s\n' 'poetry -C /build run cgt-calc "$@"' > /bin/cgt-calc \
 && chmod +x /bin/cgt-calc

WORKDIR /data
ENTRYPOINT ["/bin/bash"]
