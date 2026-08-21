# Docker

Prebuilt images with LaTeX included are published to GitHub Container Registry for amd64 and arm64
(Apple silicon), in case you would rather not have a systemwide LaTeX installation (or don't want to
interfere with an existing one). Navigate to where you store your transaction data and drop into a
shell with `cgt-calc` installed on `$PATH`:

```shell
$ cd ~/Taxes/Transactions
$ docker run --rm -it -v "$PWD":/data ghcr.io/kapji/capital-gains-calculator
a4800eca1914:/data# cgt-calc [...]
```

This will create a temporary Docker container with the current directory on the host (where your
transaction data is) mounted inside the container at `/data`. Follow the usage instructions below as
normal, and when you're done, simply exit the shell. You will be dropped back into the shell on your
host, with your output report PDF etc.

You can also run a single command directly:

```shell
$ docker run --rm -v "$PWD":/data ghcr.io/kapji/capital-gains-calculator -lc "cgt-calc [...]"
```

## Available tags

- `latest`: the latest release
- `X.Y.Z`: specific releases, pin one to reproduce a previously generated report
- `edge`: the latest development build from the `main` branch

## Building locally

To build the image locally instead, run this with the cloned repository as the current working
directory:

```shell
$ docker buildx build --tag capital-gains-calculator .
$ docker run --rm -it -v "$PWD":/data capital-gains-calculator
```
