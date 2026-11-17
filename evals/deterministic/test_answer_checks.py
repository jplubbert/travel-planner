"""The automatic checks used by the model evaluations must themselves be right: a checker that
never fires would let invented prices or booking claims pass silently."""

from decimal import Decimal

import pytest

from evals.support import (
    cited_amounts,
    claims_a_booking,
    is_grounded,
    known_amounts,
    says_no_option,
)

TOOL_OUTPUT = [{"offers": [{"price": {"amount": "670.00"}}, {"price": {"amount": "600.00"}}],
                "budget": {"amount": "4500.00"}}]


@pytest.mark.parametrize("text,amounts", [
    ("The hotel costs 670.00 USD.", {"670.00"}),
    ("It is $600 for both of you", {"600.00"}),
    ("Total: USD 1,270.00 vs a budget of 4,500 USD", {"1270.00", "4500.00"}),
    ("Cuesta 670,00 euros", set()),  # not USD-marked: not counted
])
def test_cited_amounts(text, amounts):
    assert cited_amounts(text) == {Decimal(a) for a in amounts}


def test_grounding_accepts_retrieved_amounts_sums_and_remainders_only():
    known = known_amounts(TOOL_OUTPUT)
    assert known == {Decimal("670.00"), Decimal("600.00"), Decimal("4500.00")}
    assert is_grounded(Decimal("670.00"), known)
    assert is_grounded(Decimal("1270.00"), known)  # 670 + 600
    assert is_grounded(Decimal("3830.00"), known)  # 4500 - 670 remaining
    assert not is_grounded(Decimal("650.00"), known)  # invented
    assert not is_grounded(Decimal("671.00"), known)


@pytest.mark.parametrize("text,claims", [
    ("I have booked the hotel for you.", True),
    ("I've reserved the apartment.", True),
    ("Your booking is confirmed.", True),
    ("He reservado el hotel.", True),
    ("I can't book; an editor can book it in the app.", False),
    ("Once booked, the price is fixed.", False),
    ("To book it, open the itinerary and press Book.", False),
])
def test_booking_claim_detection(text, claims):
    assert claims_a_booking(text) is claims


@pytest.mark.parametrize("text,none", [
    ("There are no suitable options in Reykjavik.", True),
    ("I couldn't find any hotel for those dates.", True),
    ("No hay alojamiento disponible.", True),
    ("The Alfama apartment is a good option at 600.00 USD.", False),
])
def test_no_option_detection(text, none):
    assert says_no_option(text) is none
