# Docker

The published Docker image includes cgt-calc, Python and LaTeX. Use it if you already have Docker
and do not want to install these tools on your computer. Images are available for amd64 and arm64
(Apple silicon).

Open a terminal in the directory containing your transaction files, then start the container:

```shell
cd ~/Taxes/Transactions
docker run --rm -it -v "$PWD":/data ghcr.io/cgt-calc/capital-gains-calculator
```

The container opens a shell in `/data`, which is the directory you started from. Run cgt-calc there
using the normal [report instructions](usage.md). For example:

```shell
cgt-calc --year 2024 --schwab-file schwab_transactions.csv
```

The generated `out/` directory remains on your computer after the container stops. Run `exit` when
you are finished.

You can also run a single command directly:

```shell
docker run --rm -v "$PWD":/data ghcr.io/cgt-calc/capital-gains-calculator -lc \
  "cgt-calc --year 2024 --schwab-file schwab_transactions.csv"
```

## Available tags

- `latest`: the latest release
- `X.Y.Z`: specific releases, pin one to reproduce a previously generated report
- `edge`: the latest development build from the `main` branch

## Other container runtimes

These are ordinary Linux OCI images, so they also work with Podman, Colima, or Apple's
[`container`](https://github.com/apple/container): substitute the runtime name for `docker` in the
commands above. There are no separate macOS or Windows images because these runtimes all run Linux
containers in a lightweight VM.

## Building locally

To build the image locally instead, run this with the cloned repository as the current working
directory:

```shell
docker buildx build --tag capital-gains-calculator .
docker run --rm -it -v "$PWD":/data capital-gains-calculator
```
