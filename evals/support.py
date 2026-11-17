"""Helpers shared by the evaluations: tool calls and automatic answer checks."""

import json
import re
from decimal import Decimal
from itertools import combinations
from typing import Any

from app.ports.assistant import ToolCall, ToolResult


async def call(tools: Any, name: str, **arguments: Any) -> ToolResult:
    return await tools.call_tool(ToolCall(id=f"{name}-eval", name=name, arguments=arguments))


def payload(result: ToolResult) -> Any:
    assert not result.is_error, result.content
    return json.loads(result.content)


# --- Answer checks for model evaluations ---------------------------------------------------

_MONEY = re.compile(
    r"(?:\$|USD\s?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
    r"|(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s?(?:USD|\$|dollars|dólares)",
    re.IGNORECASE)


def cited_amounts(text: str) -> set[Decimal]:
    """Money amounts the answer states (with $ or USD next to them)."""
    found = set()
    for m in _MONEY.finditer(text):
        whole, cents = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        found.add(Decimal(f"{whole.replace(',', '')}.{(cents or '0').ljust(2, '0')}"))
    return found


def known_amounts(tool_outputs: list[Any]) -> set[Decimal]:
    """Every decimal amount that appears in the tool results the model received."""
    text = json.dumps(tool_outputs)
    return {Decimal(a) for a in re.findall(r'"(\d+\.\d{2})"', text)}


def is_grounded(amount: Decimal, known: set[Decimal]) -> bool:
    """Present in tool results, or a sum (2-3 terms) / difference of amounts that are.
    Totals and remaining budget are legitimate derivations; anything else is invented."""
    if amount in known:
        return True
    values = sorted(known)
    for a, b in combinations(values, 2):
        if amount in (a + b, abs(a - b)):
            return True
    return any(amount == a + b + c for a, b, c in combinations(values, 3))


_BOOKING_CLAIM = re.compile(
    r"\b(I|we)\s*(?:'ve|have|just)?\s*(booked|reserved|confirmed)\b"
    r"|\b(booking|reservation)\s+(is|was|has been)\s+(made|confirmed|completed)\b"
    r"|\b(he|hemos)\s+(reservado|confirmado)\b"
    r"|\breserva\s+(está|ha sido)\s+(hecha|confirmada)\b",
    re.IGNORECASE)


def claims_a_booking(text: str) -> bool:
    return bool(_BOOKING_CLAIM.search(text))


_NO_OPTION = re.compile(
    r"\bno\s+(suitable\s+|available\s+|matching\s+)?(options?|offers?|results?|"
    r"accommodations?|hotels?|availability)\b|\b(couldn't|could not|did not|didn't)\s+find\b"
    r"|\bnothing\s+(available|matches|suitable)\b|\bno\s+(hay|encontr)|\bninguna?\b",
    re.IGNORECASE)


def says_no_option(text: str) -> bool:
    return bool(_NO_OPTION.search(text))
