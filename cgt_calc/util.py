"""Utility functions."""

import decimal
from decimal import Decimal
from pathlib import Path
from typing import TextIO

import iso4217parse


def round_decimal(value: Decimal, digits: int = 0) -> Decimal:
    """Round decimal to given precision."""
    with decimal.localcontext() as ctx:
        ctx.rounding = decimal.ROUND_HALF_UP
        return Decimal(round(value, digits))


def normalize_amount(amount: Decimal) -> Decimal:
    """Normalize amount to prevent unbounded precision growth.

    Financial amounts are rounded to 10 decimal places, which is far more
    precision than needed for real-world financial calculations, while preventing
    precision from growing unbounded through currency conversions with repeating
    decimals (e.g., GBP/USD conversions).

    This allows using Python's default 28-digit Decimal precision instead of
    requiring 50+ digits.
    """
    return round_decimal(amount, 10)


def strip_zeros(value: Decimal) -> str:
    """Strip trailing zeros from Decimal."""
    return f"{value:.10f}".rstrip("0").rstrip(".")


def exact_str(value: Decimal) -> str:
    """Format a Decimal in full, for output that is meant to be read back.

    `strip_zeros` is for display and stops at ten decimal places, which
    turns a small enough value into "0". A value that will be parsed again
    has to keep every digit it has, in plain notation so the reader does
    not need to cope with exponents. Formatting never rounds, unlike
    `normalize()`, which applies the context precision.
    """
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def luhn_check_digit(payload: str) -> int:
    """Return the check digit for a string of digits using the Luhn algorithm.

    Reference: https://en.wikipedia.org/wiki/Luhn_algorithm
    """
    if len(payload) % 2 == 1:
        payload = f"0{payload}"  # zero pad so length is even
    checksum = 0

    luhn_even_digit_max_value = 9
    luhn_even_digit_multiplier = 2
    for idx, digit_char in enumerate(payload[::-1]):
        digit = int(digit_char)
        if idx % 2 == 0:
            digit *= luhn_even_digit_multiplier
            if digit > luhn_even_digit_max_value:
                digit -= luhn_even_digit_max_value
        checksum += digit

    return (
        10 - (checksum % 10)
    ) % 10  # using mod operator twice ensures the check digit is < 10


def approx_equal(
    val_a: Decimal, val_b: Decimal, approx_quantity: Decimal = Decimal("0.01")
) -> bool:
    """Check if two decimals are equal within approx_quantity.

    It is not clear how Schwab or other brokers round the dollar value,
    so assume the values are equal if they are within approx_quantity.
    Defaults to 0.01.
    """
    return abs(val_a - val_b) < approx_quantity


def open_with_parents(path: Path, *, clear_content: bool = True) -> TextIO:
    """Open a file for writing, creating parent directories if they do not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w" if clear_content else "r+", encoding="utf8")


def is_currency(currency_str: str) -> bool:
    """Check if the input string is a valid currency."""
    return bool(iso4217parse.by_alpha3(currency_str))
