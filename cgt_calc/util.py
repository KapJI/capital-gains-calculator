"""Utility functions."""

import decimal
from decimal import Decimal
import re

import iso4217parse


def round_decimal(value: Decimal, digits: int = 0) -> Decimal:
    """Round decimal to given precision."""
    with decimal.localcontext() as ctx:
        ctx.rounding = decimal.ROUND_HALF_UP
        return Decimal(round(value, digits))


def strip_zeros(value: Decimal) -> str:
    """Strip trailing zeros from Decimal."""
    return f"{value:.10f}".rstrip("0").rstrip(".")


def luhn_check_digit(payload: str) -> int:
    """Return the check digit given a string of numbers given the Luhn Algorithm.

    Reference: https://en.wikipedia.org/wiki/Luhn_algorithm
    """
    if len(payload) % 2 == 1:
        payload = f"0{payload}"  # zero pad so length is even
    checksum = 0

    LUHN_EVEN_DIGIT_MAX_VALUE = 9
    LUHN_EVEN_DIGIT_MULTIPLIER = 2
    for idx, digit_char in enumerate(payload[::-1]):
        digit = int(digit_char)
        if idx % 2 == 0:
            digit *= LUHN_EVEN_DIGIT_MULTIPLIER
            if digit > LUHN_EVEN_DIGIT_MAX_VALUE:
                digit -= LUHN_EVEN_DIGIT_MAX_VALUE
        checksum += digit

    return (
        10 - (checksum % 10)
    ) % 10  # using mod operator twice asserts the check digit is < 10


def is_isin(isin: str) -> bool:
    """Validate if a string is a valid ISIN."""
    # https://en.wikipedia.org/wiki/International_Securities_Identification_Number
    ISIN_REGEX = r"^([A-Z]{2})([A-Z0-9]{9})([0-9])$"
    ISIN_CHAR_IDXS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    if not re.match(ISIN_REGEX, isin):
        return False
    payload = isin[:11]
    check_digit = int(isin[11])

    payload = "".join(str(ISIN_CHAR_IDXS.index(c)) for c in list(payload))
    return luhn_check_digit(payload) == check_digit


def approx_equal(
    val_a: Decimal, val_b: Decimal, approx_quantity: Decimal = Decimal("0.01")
) -> bool:
    """Calculate if two decimal are the same within approx_quantity input.

    It is not clear how Schwab or other brokers round the dollar value,
    so assume the values are equal if they are within approx_quantity input.
    Defaults to 0.01
    """
    return abs(val_a - val_b) < approx_quantity


def is_currency(currency_str: str) -> bool:
    """Check if the input string is a valid currency."""
    return bool(iso4217parse.by_alpha3(currency_str))
