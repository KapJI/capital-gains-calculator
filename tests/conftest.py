"""Shared test configuration."""

import os

# Force plain CLI output regardless of the environment running the tests:
# expected-output fixtures are byte-exact and stderr assertions match on the
# "WARNING:"/"ERROR:" prefixes, so colour and emoji must never leak in.
os.environ["NO_COLOR"] = "1"
os.environ.pop("FORCE_COLOR", None)
