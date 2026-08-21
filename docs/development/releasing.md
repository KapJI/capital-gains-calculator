# Release Process

(Maintainers only.)

Releases are created from draft GitHub releases created by
[Release Drafter](https://github.com/release-drafter/release-drafter). When a release is published,
GitHub Actions will automatically:

1. Extract the version from the release tag (e.g. `v1.2.3`)
2. Run tests and checks
3. Build and publish the package to PyPI using `uv publish` and
   [Trusted Publisher](https://docs.pypi.org/trusted-publishers/) on PyPI
