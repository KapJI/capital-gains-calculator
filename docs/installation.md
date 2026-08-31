# Installation

## Prerequisites

- **Python 3.12** or newer (tested on 3.12, 3.13, and 3.14)
- **LaTeX** if you want a PDF report. Install `pdflatex` using the instructions below. You can run
    cgt-calc without it by using `--no-report`.

## Install cgt-calc

Install the calculator with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```shell
uv tool install cgt-calc
```

Alternatively, install it with [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html):

```shell
pipx install cgt-calc
```

Or use [pip](https://pip.pypa.io/en/stable/getting-started/) inside an active Python virtual
environment:

```shell
python -m pip install cgt-calc
```

Or run it directly without installing:

```shell
uvx cgt-calc
```

## Nix

The Nix community maintains a
[`cgt-calc` package in nixpkgs](https://search.nixos.org/packages?query=cgt-calc), which bundles
`pdflatex` so no separate LaTeX install is needed:

```shell
nix run nixpkgs#cgt-calc
```

## Installing LaTeX

### macOS

```shell
brew install --cask mactex-no-gui
```

### Debian/Ubuntu

```shell
apt install texlive-latex-base
```

### Windows

[Install MiKTeX.](https://miktex.org/download)

## Check the installation

Run:

```shell
cgt-calc --version
```

If you use `uvx` instead of installing cgt-calc, run `uvx cgt-calc --version`. Once the command
prints a version, choose your [broker](brokers/index.md) and follow the [report guide](usage.md).

## Shell completions (optional)

`cgt-calc` can generate a tab completion script for `bash`, `zsh`, `fish`, `tcsh` and PowerShell.
Save it where your shell looks for completions, e.g.:

```shell
# bash
cgt-calc --print-completion bash > ~/.local/share/bash-completion/completions/cgt-calc

# zsh (the target directory must be in your $fpath)
cgt-calc --print-completion zsh > ~/.zfunc/_cgt-calc

# fish
cgt-calc --print-completion fish > ~/.config/fish/completions/cgt-calc.fish
```

For PowerShell, create the completions directory if needed and generate the script:

```powershell
$completionDir = "$HOME\.config\powershell\completions"
New-Item -ItemType Directory -Force $completionDir | Out-Null
cgt-calc --print-completion powershell > "$completionDir\cgt-calc.ps1"
```

Then add the following line to your PowerShell profile (`$PROFILE`):

```powershell
. "$HOME\.config\powershell\completions\cgt-calc.ps1"
```

Regenerate the script after upgrading to pick up new options.
