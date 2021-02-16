#!/usr/bin/env python3

import decimal
from decimal import Decimal


def round_decimal(value: Decimal, digits: int = 0) -> Decimal:
    with decimal.localcontext() as ctx:
        ctx.rounding = decimal.ROUND_HALF_UP
        return Decimal(round(value, digits))
