"""Test Schwab action type mapping.

Tests the conversion from Schwab CSV action strings to internal ActionType enum values.
"""

from pathlib import Path

from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import action_from_str


class TestActionMapping:
    """Test action type mapping from Schwab action strings."""

    def test_bond_interest_mapping(self) -> None:
        """Test that 'Bond Interest' maps to INTEREST."""
        result = action_from_str("Bond Interest", Path("test.csv"))
        assert result == ActionType.INTEREST

    def test_credit_interest_mapping(self) -> None:
        """Test that 'Credit Interest' maps to INTEREST."""
        result = action_from_str("Credit Interest", Path("test.csv"))
        assert result == ActionType.INTEREST
