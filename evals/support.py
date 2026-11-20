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


# --- US-11: relating options to dates and restrictions, and flagging conflicts ---------------
# Heuristics for English and Spanish answers. They are deliberately simple and have their own
# tests (evals/deterministic/test_answer_checks.py); see evals/README.md for their limits.

_MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december"]
_MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
              "septiembre", "octubre", "noviembre", "diciembre"]
_NUMBERS_EN = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
               "ten", "eleven", "twelve", "thirteen", "fourteen"]
_NUMBERS_ES = ["cero", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
               "nueve", "diez", "once", "doce", "trece", "catorce"]
# Words too common in offer titles and restrictions to identify one.
_GENERIC = {"booking", "simulated", "included", "guest", "guests", "stay", "room", "rooms",
            "night", "nights", "hotel", "travelers", "trip", "tour", "day", "four", "three",
            "five", "beds", "after", "before", "with", "from", "only", "per", "the", "and"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúñü0-9]+", text.lower())


def _distinctive(text: str) -> list[str]:
    return [w for w in _words(text) if (len(w) >= 4 or w.isdigit()) and w not in _GENERIC]


def relates_to_dates(text: str, starts_on: Any, ends_on: Any) -> bool:
    """The answer ties its proposal to the trip dates: it names a trip date (ISO, "June 10",
    "10 June", "Jun 10", "10 de junio", "10/06") or the number of nights of the stay."""
    lowered = text.lower()
    for d in (starts_on, ends_on):
        en, es = _MONTHS_EN[d.month - 1], _MONTHS_ES[d.month - 1]
        patterns = [d.isoformat(), rf"\b{en}\s+{d.day}\b",
                    rf"\b{d.day}(st|nd|rd|th)?\s+(of\s+)?{en}\b", rf"\b{en[:3]}\.?\s+{d.day}\b",
                    rf"\b{d.day}\s+{en[:3]}\b",
                    rf"\b{d.day}\s+de\s+{es}\b", rf"\b0?{d.day}/0?{d.month}\b",
                    rf"\b0?{d.month}/0?{d.day}\b"]
        if any(re.search(p, lowered) for p in patterns):
            return True
    nights = (ends_on - starts_on).days
    counts = [str(nights)]
    if nights < len(_NUMBERS_EN):
        counts += [_NUMBERS_EN[nights], _NUMBERS_ES[nights]]
    return bool(re.search(rf"\b({'|'.join(counts)})[\s-]+(nights?|noches?)\b", lowered))


def mentioned_offers(text: str, offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Offers the answer proposes: it cites the offer's price or every distinctive word of
    its title (e.g. "Alfama" for "Four-night Alfama apartment")."""
    amounts = cited_amounts(text)
    words = set(_words(text))
    found = []
    for offer in offers:
        price = Decimal(offer["price"]["amount"])
        key = _distinctive(offer["title"])
        if price in amounts or (key and all(w in words for w in key)):
            found.append(offer)
    return found


def cited_restrictions(text: str, restrictions: list[str]) -> set[str]:
    """Restrictions the answer mentions: at least half of a restriction's distinctive words
    appear ("non-refundable" cites "Non-refundable simulated booking")."""
    words = set(_words(text))
    cited = set()
    for restriction in restrictions:
        key = _distinctive(restriction)
        if key and sum(w in words for w in key) * 2 >= len(key):
            cited.add(restriction)
    return cited


_CONFLICT = re.compile(
    r"overlap|conflict|clash|double[- ]book|same time|at once|simultaneous"
    r"|solap|conflicto|coinciden|a la vez|al mismo tiempo",
    re.IGNORECASE)


def names_item(text: str, title: str) -> bool:
    words = set(_words(text))
    return any(w in words for w in _distinctive(title))


def flags_conflict(text: str, titles: list[str]) -> bool:
    """The answer points out a schedule conflict and names every item involved."""
    return bool(_CONFLICT.search(text)) and all(names_item(text, t) for t in titles)


_MISSING = re.compile(
    r"\b(no|not|without|missing|lacks?|lacking|uncovered|yet to|haven't|have not|don't have|"
    r"do not have|sin|falta|faltan|todavía no|aún no)\b",
    re.IGNORECASE)
_LODGING = re.compile(
    r"\b(accommodations?|lodging|hotels?|place to stay|somewhere to stay|where to stay|"
    r"alojamientos?|hospedaje|dónde dormir)\b",
    re.IGNORECASE)


def flags_missing_accommodation(text: str, starts_on: Any, ends_on: Any) -> bool:
    """The answer points out a gap: some sentence says accommodation is missing, and the
    answer ties it to the trip's nights or dates."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    gap = any(_MISSING.search(s) and _LODGING.search(s) for s in sentences)
    return gap and relates_to_dates(text, starts_on, ends_on)


_CHANGE_CLAIM = re.compile(
    r"\b(I|we)\s*(?:'ve|have|just)?\s*(removed|deleted|moved|rescheduled|changed|updated|"
    r"replaced|added)\b"
    r"|\b(he|hemos)\s+(quitado|eliminado|movido|cambiado|actualizado|añadido)\b",
    re.IGNORECASE)


def claims_a_change(text: str) -> bool:
    """The answer says the assistant itself modified the itinerary (it cannot)."""
    return bool(_CHANGE_CLAIM.search(text))


_PREFERENCE_REF = re.compile(
    r"\b(prefer\w*|requirements?|requested|asked for|you want(ed)?|wish(es)?|"
    r"prefier\w*|preferencias?|pidieron|pediste|requisitos?)\b",
    re.IGNORECASE)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def relates_to_preferences(text: str, preferences: str) -> bool:
    """The answer relates its proposal to the group's saved preferences: at least half of
    their distinctive words appear (singular or plural), and some sentence that refers to
    a preference ("you prefer", "your requirement", "preferencia") names one of them.
    Mentioning "shared bathroom" as an offer's restriction is not enough on its own."""
    key = {_stem(w) for w in _distinctive(preferences)}
    if not key:
        return False
    found = {_stem(w) for w in _words(text)} & key
    if len(found) * 2 < len(key):
        return False
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    return any(_PREFERENCE_REF.search(s) and {_stem(w) for w in _words(s)} & key
               for s in sentences)


# --- Composite checks used by the model evaluations (and tested with a scripted model) -------


def check_relates_to_dates_and_restrictions(body: dict[str, Any], starts_on: Any,
                                            ends_on: Any) -> None:
    """US-11: the answer proposes retrieved options, ties them to the trip dates or nights,
    and mentions at least one restriction of every proposed option that has any."""
    searches = [c for c in body["toolCalls"]
                if c["name"] == "search_offers" and not c["isError"]]
    assert searches, "expected a successful search"
    offers = [o for c in searches for o in c["result"]["offers"]]
    proposed = mentioned_offers(body["answer"], offers)
    assert proposed, "expected the answer to propose at least one retrieved option"
    assert relates_to_dates(body["answer"], starts_on, ends_on), \
        "the answer does not tie the options to the trip dates or nights"
    for offer in proposed:
        if offer["restrictions"]:
            assert cited_restrictions(body["answer"], offer["restrictions"]), \
                f"restrictions of {offer['title']!r} not mentioned: {offer['restrictions']}"


def check_points_out_conflict(body: dict[str, Any], titles: list[str]) -> None:
    """US-11: the answer read the itinerary, points out the conflict naming every item
    involved, and does not claim to have changed anything."""
    assert any(c["name"] == "get_itinerary" and not c["isError"] for c in body["toolCalls"]), \
        "expected the itinerary to be read"
    assert flags_conflict(body["answer"], titles), \
        "the answer does not point out the overlap naming both activities"
    assert not claims_a_change(body["answer"]), body["answer"]


def check_points_out_missing_accommodation(body: dict[str, Any], starts_on: Any,
                                           ends_on: Any) -> None:
    """US-11 (gaps): the answer read the itinerary, says the nights have no accommodation,
    tied to the trip's nights or dates, and does not claim to have changed anything."""
    assert any(c["name"] == "get_itinerary" and not c["isError"] for c in body["toolCalls"]), \
        "expected the itinerary to be read"
    assert flags_missing_accommodation(body["answer"], starts_on, ends_on), \
        "the answer does not point out the nights without accommodation"
    assert not claims_a_change(body["answer"]), body["answer"]


def check_relates_to_preferences(body: dict[str, Any], preferences: str) -> None:
    """US-11 (preferences): the preferences reached the model through get_trip (the chat
    message does not contain them), the answer proposes a retrieved option and relates it
    to the saved preferences, and does not claim to have changed anything."""
    trips = [c for c in body["toolCalls"] if c["name"] == "get_trip" and not c["isError"]]
    assert trips, "expected the trip to be read"
    assert trips[0]["result"]["preferences"] == preferences, trips[0]["result"]
    searches = [c for c in body["toolCalls"]
                if c["name"] == "search_offers" and not c["isError"]]
    assert searches, "expected a successful search"
    offers = [o for c in searches for o in c["result"]["offers"]]
    assert mentioned_offers(body["answer"], offers), \
        "expected the answer to propose at least one retrieved option"
    assert relates_to_preferences(body["answer"], preferences), \
        f"the answer does not relate its proposal to the saved preferences: {preferences!r}"
    assert not claims_a_change(body["answer"]), body["answer"]
