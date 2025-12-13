"""CUSIP bond price adjustment logic for Schwab transactions.

Schwab reports bond prices per $100 face value, while capital gains calculations
need prices per $1 face value. This module handles the conversion and validation.
"""

from __future__ import annotations

from decimal import Decimal
import logging
from typing import Final

LOGGER = logging.getLogger(__name__)

# CUSIP bond pricing constants
CUSIP_SYMBOL_LENGTH: Final = 9
BOND_PRICE_DIVISOR: Final = 100
MIN_ACCRUED_INTEREST_THRESHOLD: Final = Decimal("0.01")
MAX_ACCRUED_INTEREST_BUY: Final = Decimal("0.01")
MAX_ACCRUED_INTEREST_SELL: Final = Decimal(100)
BOND_PRICE_TOLERANCE_RATIO: Final = Decimal("0.5")

# Valid CUSIP characters for positions 7-8 (letters + special chars for fixed income)
CUSIP_ALPHA_CHARS: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ*@#"


def _validate_cusip_check_digit(cusip: str) -> bool:
    """Validate CUSIP check digit using the Luhn-like algorithm.

    The 9th character is a check digit calculated from the first 8 characters:
    - Digits (0-9) have value as-is
    - Letters A-Z map to 10-35
    - Special chars: * = 36, @ = 37, # = 38
    - Values at odd positions (1, 3, 5, 7) are doubled
    - For each value, sum (val % 10) + (val // 10)
    - Check digit = (10 - (total_sum % 10)) % 10
    """
    total_sum = 0
    for idx, char in enumerate(cusip[:-1].upper()):
        if char.isdigit():
            val = int(char)
        elif char in CUSIP_ALPHA_CHARS:
            val = CUSIP_ALPHA_CHARS.index(char) + 10
        else:
            # Invalid character
            return False

        # Double values at odd positions
        if idx % 2 != 0:
            val *= 2

        # Sum the digits
        total_sum += (val % 10) + (val // 10)

    check_digit = (10 - (total_sum % 10)) % 10
    return str(check_digit) == cusip[-1]


def _is_cusip_symbol(symbol: str | None) -> bool:
    """Check if symbol is a valid CUSIP identifier.

    CUSIP format (9 characters):
    - Characters 1-6: Issuer identifier (alphanumeric)
    - Characters 7-8: Issue identifier (alphanumeric + special chars *@# for bonds)
    - Character 9: Check digit (validated using Luhn-like algorithm)

    Returns True if symbol is exactly 9 characters and passes check digit validation.
    """
    if not symbol or len(symbol) != CUSIP_SYMBOL_LENGTH:
        return False

    return _validate_cusip_check_digit(symbol)


def _is_amount_in_range(amount: Decimal, min_val: Decimal, max_val: Decimal) -> bool:
    """Check if amount is within expected range accounting for accrued interest."""
    return min_val <= amount <= max_val


def _is_amount_within_tolerance(
    amount: Decimal,
    expected_gross: Decimal,
    tolerance_ratio: Decimal,
) -> bool:
    """Check if amount is within percentage tolerance of expected gross.

    This handles cases where accrued interest exceeds the fixed range.
    """
    return (
        abs(abs(amount) - abs(expected_gross)) < abs(expected_gross) * tolerance_ratio
    )


def _validate_bond_amount(
    amount: Decimal,
    expected_gross: Decimal,
    fees: Decimal,
) -> bool:
    """Validate bond transaction amount using two strategies.

    Strategy 1: Fixed accrued interest range (±$0.01 for buys, ±$100 for sells)
      - Buy: amount = -(quantity * price + fees + accrued_interest)
        We expect accrued interest to be very small (±$0.01) for buy transactions.
      - Sell: amount = quantity * price - fees - accrued_interest
        We allow wider range (±$100) because accrued interest can vary significantly
        based on time since last coupon payment (up to 6 months for semi-annual bonds).

    Strategy 2: Percentage tolerance (50% of expected gross)
      - Fallback validation for cases where accrued interest exceeds normal range.

    Returns True if either strategy passes.
    """
    if amount < 0:
        # Buy: amount = -(quantity * price + fees + accrued_interest)
        expected_min = -(abs(expected_gross) + abs(fees) + MAX_ACCRUED_INTEREST_BUY)
        expected_max = -(abs(expected_gross) + abs(fees) - MAX_ACCRUED_INTEREST_BUY)
    else:
        # Sell: amount = quantity * price - fees - accrued_interest
        expected_min = abs(expected_gross) - abs(fees) - MAX_ACCRUED_INTEREST_SELL
        expected_max = abs(expected_gross) - abs(fees) + MAX_ACCRUED_INTEREST_SELL

    return _is_amount_in_range(
        amount, expected_min, expected_max
    ) or _is_amount_within_tolerance(amount, expected_gross, BOND_PRICE_TOLERANCE_RATIO)


def adjust_cusip_bond_price(
    symbol: str | None,
    price: Decimal | None,
    quantity: Decimal | None,
    amount: Decimal | None,
    fees: Decimal,
) -> tuple[Decimal | None, Decimal]:
    """Adjust price and fees for CUSIP bond symbols.

    CUSIP symbols (9 alphanumeric chars starting with digit) have price per $100 face value.
    This function converts to price per $1 face value by dividing by 100.

    It also validates the amount matches the expected value (quantity * price/100) +/- fees
    +/- accrued interest, and adds accrued interest to fees if present.

    Args:
        symbol: The security symbol
        price: The price per $100 face value
        quantity: The transaction quantity
        amount: The transaction amount (negative for buys, positive for sells)
        fees: The transaction fees

    Returns:
        Tuple of (adjusted_price, adjusted_fees). If validation fails or not a CUSIP,
        returns (original_price, original_fees).

    Example:
        >>> # Bond buy at $99.1727 per $100 face value
        >>> adjust_cusip_bond_price("91282CMF5", Decimal("9917.27"),
        ...                         Decimal("40000"), Decimal("-3966908.00"),
        ...                         Decimal("0"))
        (Decimal("99.1727"), Decimal("0"))

    """
    # Early return if not a CUSIP or missing price
    if not _is_cusip_symbol(symbol) or price is None:
        return (price, fees)

    # Mypy: at this point price is guaranteed to be non-None due to check above
    assert price is not None
    adjusted_price = price / BOND_PRICE_DIVISOR

    # If we can't validate, apply adjustment anyway (trusted data)
    if quantity is None or amount is None:
        LOGGER.debug("Bond %s: applied /100 price adjustment (no validation)", symbol)
        return (adjusted_price, fees)

    # Validate the amount matches expected calculation
    expected_gross = quantity * adjusted_price

    if not _validate_bond_amount(amount, expected_gross, fees):
        LOGGER.warning(
            "CUSIP symbol %s: amount validation failed (expected ~$%.2f, got $%.2f), "
            "not applying /100 price adjustment",
            symbol,
            float(expected_gross - fees if amount < 0 else expected_gross + fees),
            float(amount),
        )
        return (price, fees)

    # Validation passed - calculate accrued interest
    expected_amount = quantity * adjusted_price
    accrued_interest = abs(amount) - abs(expected_amount) - abs(fees)

    if accrued_interest > MIN_ACCRUED_INTEREST_THRESHOLD:
        adjusted_fees = fees + accrued_interest
        LOGGER.debug(
            "Bond %s: added accrued interest $%s to fees", symbol, accrued_interest
        )
        return (adjusted_price, adjusted_fees)

    return (adjusted_price, fees)
