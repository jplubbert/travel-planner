"""USD amounts. All money is ``Decimal`` with exactly two fractional digits; floats never."""

import re
from decimal import ROUND_HALF_UP, Decimal

CURRENCY = "USD"
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")  # numeric(12, 2)

_AMOUNT = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$")


def parse_usd(value: object) -> Decimal:
    """Parse a non-negative decimal string with at most two fractional digits."""
    if not isinstance(value, str) or not _AMOUNT.match(value):
        raise ValueError("must be a decimal string with at most 2 decimals, e.g. '1500.00'")
    amount = Decimal(value).quantize(CENT)
    if amount > MAX_AMOUNT:
        raise ValueError(f"must not exceed {MAX_AMOUNT}")
    return amount


def round_usd(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def format_usd(amount: Decimal) -> str:
    return str(round_usd(amount))
