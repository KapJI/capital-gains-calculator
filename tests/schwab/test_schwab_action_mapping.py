"""Test Schwab action type mapping.

Tests the conversion from Schwab CSV action strings to internal ActionType enum values.
"""

from pathlib import Path

from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import action_from_str


class TestActionMapping:
    """Test action type mapping from Schwab action strings."""

    def test_journaled_shares_mapping(self) -> None:
        """Test that 'Journaled Shares' maps to TRANSFER."""
        result = action_from_str("Journaled Shares", Path("test.csv"))
        assert result == ActionType.TRANSFER
