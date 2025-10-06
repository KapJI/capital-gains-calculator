# syntax=docker/dockerfile:1.7

FROM python:3.9-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash texlive-latex-base \
    && rm -rf /var/lib/apt/lists/*

# Copy uv static binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /build

# 1) Copy dependency manifests first (for caching)
COPY pyproject.toml uv.lock* /build/

# Install dependencies (no project source yet -> cacheable)
RUN --mount=type=cache,target=/root/.cache \
    uv sync --frozen --no-install-project

# 2) Now copy project source and install package
COPY . /build
RUN --mount=type=cache,target=/root/.cache \
    uv sync --frozen

# Simple CLI shim
RUN printf '%s\n' 'cd /build && uv run cgt-calc "$@"' > /bin/cgt-calc \
 && chmod +x /bin/cgt-calc

WORKDIR /data
ENTRYPOINT ["/bin/bash"]
