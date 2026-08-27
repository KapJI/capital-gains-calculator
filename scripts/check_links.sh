#!/usr/bin/env bash

# Check the links in all Markdown files with lychee.
#
# Two passes are run, the offline one first:
#   offline  - local files and heading anchors only. No network, so it is
#              deterministic and only fails on genuine mistakes in the change.
#   external - everything, including third party sites. Can fail for reasons
#              unrelated to the change under review.
#
# Shared settings live in lychee.toml. The remap resolves cgt-calc.uk URLs
# against the local docs sources, so a link to a page added in the same change
# is checked before the site is deployed. lychee requires an absolute file://
# target for a remap, hence the interpolation of the repository root below.
#
# Usage: scripts/check_links.sh [offline|external]

set -uo pipefail

root="$(git rev-parse --show-toplevel)" || exit 1
cd "$root" || exit 1

# Single quotes around the pattern keep the backslash and lychee's own $1
# capture reference literal; only the repository root is interpolated.
remap='https://cgt-calc\.uk/(.*) file://'"$root"'/docs/$1'

lychee=(uv run --only-group links lychee --remap "$remap")

status=0

check_offline() {
    echo "==> Checking local files and heading anchors"
    "${lychee[@]}" --offline --include-fragments=anchor-only './**/*.md' || status=1
}

check_external() {
    echo "==> Checking external links"
    "${lychee[@]}" './**/*.md' || status=1
}

case "${1:-all}" in
offline) check_offline ;;
external) check_external ;;
all)
    check_offline
    check_external
    ;;
*)
    echo "Usage: $0 [offline|external]" >&2
    exit 2
    ;;
esac

exit "$status"
