"""The automatic checks used by the model evaluations must themselves be right: a checker that
never fires would let invented prices or booking claims pass silently."""

from datetime import date
from decimal import Decimal

import pytest

from evals.support import (
    cited_amounts,
    cited_restrictions,
    claims_a_booking,
    claims_a_change,
    flags_conflict,
    flags_missing_accommodation,
    is_grounded,
    known_amounts,
    mentioned_offers,
    relates_to_dates,
    relates_to_preferences,
    says_no_option,
)

TOOL_OUTPUT = [{"offers": [{"price": {"amount": "670.00"}}, {"price": {"amount": "600.00"}}],
                "budget": {"amount": "4500.00"}}]


@pytest.mark.parametrize("text,amounts", [
    ("The hotel costs 670.00 USD.", {"670.00"}),
    ("It is $600 for both of you", {"600.00"}),
    ("Total: USD 1,270.00 vs a budget of 4,500 USD", {"1270.00", "4500.00"}),
    ("Cuesta 670,00 euros", set()),  # not USD-marked: not counted
    # Amounts with cents but without "$" or "USD" (missed before 2026-11-20).
    ("The Alfama apartment costs 600.00 for the group.", {"600.00"}),
    ("Remaining after the hotel: 3,830.00.", {"3830.00"}),
    ("Hotel 670.00 + apartment 600.00 = 1,270.00 USD", {"670.00", "600.00", "1270.00"}),
    # Not money: a percentage, one decimal, a version-like number.
    ("That uses 22.35% of the budget, rated 4.5, see v1.10.2.", set()),
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


# --- US-11: dates, restrictions and conflicts (fixtures from mock/provider_mock/fixtures.py) --

START, END = date(2030, 6, 10), date(2030, 6, 14)
LISBON_STAYS = [
    {"title": "Four-night central hotel stay", "price": {"amount": "670.00"},
     "restrictions": ["Non-refundable simulated booking"]},
    {"title": "Four-night Alfama apartment", "price": {"amount": "600.00"},
     "restrictions": ["No check-in after 22:00"]},
    {"title": "Four-night Baixa hostel beds", "price": {"amount": "225.60"},
     "restrictions": ["Shared bathroom"]},
    {"title": "Three-night Belém boutique hotel", "price": {"amount": "690.00"},
     "restrictions": []},
]


@pytest.mark.parametrize("text", [
    "It covers your 4 nights in Lisbon.",
    "Check-in on June 10 and check-out on June 14.",
    "From 10 June to 14 June.",
    "Llegáis el 10 de junio.",
    "Stay from 2030-06-10.",
    "All four nights are covered.",
    "Son cuatro noches.",
])
def test_relates_to_dates_recognises_trip_dates_and_nights(text):
    assert relates_to_dates(text, START, END)


@pytest.mark.parametrize("text", [
    "This is a lovely hotel in the centre.",
    "Two nights would be cheaper.",  # wrong number of nights
    "Available on June 11.",  # not a trip boundary
    "It costs 670.00 USD.",
])
def test_relates_to_dates_ignores_answers_without_the_trip_dates(text):
    assert not relates_to_dates(text, START, END)


def test_mentioned_offers_by_price_or_distinctive_title_words():
    text = ("The Alfama apartment is cheaper than the central hotel stay (670.00 USD). "
            "The cheapest is 225.60 USD.")
    titles = [o["title"] for o in mentioned_offers(text, LISBON_STAYS)]
    assert titles == ["Four-night central hotel stay", "Four-night Alfama apartment",
                      "Four-night Baixa hostel beds"]
    assert mentioned_offers("No accommodation fits.", LISBON_STAYS) == []


@pytest.mark.parametrize("text,cited", [
    ("Note that it is non-refundable.", {"Non-refundable simulated booking"}),
    ("You cannot check in after 22:00.", {"No check-in after 22:00"}),
    # Known limit, on the strict side: a paraphrase without the time is not counted, so
    # the model evaluation fails and the printed answer gets reviewed (never a silent pass).
    ("Late check-in is not possible.", set()),
    ("Beds with a shared bathroom.", {"Shared bathroom"}),
    ("A simulated booking for your group.", set()),  # generic words only
    ("Great location and price.", set()),
])
def test_cited_restrictions(text, cited):
    every = [r for o in LISBON_STAYS for r in o["restrictions"]]
    assert cited_restrictions(text, every) == cited


@pytest.mark.parametrize("text,flagged", [
    ("The Historic tram tour overlaps with the Sintra day trip on June 11.", True),
    ("Careful: your tram tour and Sintra trip are at the same time.", True),
    ("El tour en tranvía se solapa con la excursión a Sintra.", False),  # "tranvía" != "tram"
    ("There is a conflict on June 11.", False),  # does not name the items
    ("Your tram tour and Sintra day trip look great.", False),  # names them, no conflict
])
def test_flags_conflict(text, flagged):
    assert flags_conflict(text, ["Historic tram tour", "Sintra day trip"]) is flagged


@pytest.mark.parametrize("text,claims", [
    ("I removed the tram tour to fix the overlap.", True),
    ("I've moved the Sintra trip to June 12.", True),
    ("He quitado el tour.", True),
    ("You could remove the tram tour or move it to another day.", False),
    ("Consider rescheduling one of them in the app.", False),
])
def test_claims_a_change(text, claims):
    assert claims_a_change(text) is claims


@pytest.mark.parametrize("text,flagged", [
    ("You have no accommodation for your 4 nights in Lisbon.", True),
    ("Note: the nights of June 10 to 13 are still without a hotel.", True),
    ("There is nowhere booked yet... Missing: a place to stay from June 10.", True),
    ("Os falta alojamiento para las cuatro noches.", True),
    ("Your hotel covers all 4 nights.", False),  # no missing-word in that sentence
    # A missing-word and a lodging word in different sentences are not a gap.
    ("Your hotel covers all 4 nights. There is no conflict on June 11.", False),
    ("You have no accommodation yet.", False),  # not tied to the trip's nights or dates
    ("There is no conflict on June 10.", False),  # missing-word, but not about lodging
    ("Your tram tour overlaps with the Sintra day trip.", False),
])
def test_flags_missing_accommodation(text, flagged):
    assert flags_missing_accommodation(text, START, END) is flagged


PREFS = "Private bathroom; no hostels."


@pytest.mark.parametrize("text,related", [
    ("Since you prefer a private bathroom, the Alfama apartment fits; I left out the Baixa "
     "hostel.", True),
    ("Your requirement: no hostels and a private bathroom. The Central hotel meets it.", True),
    ("Según vuestras preferencias (private bathroom, no hostels), el apartamento de Alfama "
     "encaja.", True),
    # Strict side: a translation shares only "hostel" with the saved text (known limitation).
    ("Prefieren evitar hostels: el apartamento de Alfama tiene baño privado.", False),
    # Names the words but never refers to them as the group's preference.
    ("The Baixa hostel has a shared bathroom; the Alfama apartment has a private one.", False),
    # Refers to preferences, but to none of the saved ones.
    ("Given your preferences, the Alfama apartment is central and quiet.", False),
    # Only one of the three distinctive words.
    ("You prefer hostels? The Baixa one is cheap.", False),
    ("", False),
])
def test_relates_to_preferences(text, related):
    assert relates_to_preferences(text, PREFS) is related


def test_empty_preferences_never_count_as_related():
    assert relates_to_preferences("You prefer a private bathroom.", "") is False


# --- False positives found on 2026-11-20 (each case failed before its fix) -------------------

CONFLICT_TITLES = ["Historic tram tour", "Sintra day trip"]


@pytest.mark.parametrize("text", [
    "There is no conflict between the Historic tram tour and the Sintra day trip.",
    "The Historic tram tour does not overlap with the Sintra day trip.",
    "The tram tour and the Sintra day trip don't clash.",
    "The tram tour fits without any conflict with the Sintra day trip.",
    "El Historic tram tour y el Sintra day trip no se solapan.",
    "Sin conflictos: el Historic tram tour y el Sintra day trip están en horarios distintos.",
    "No hay ningún conflicto entre el Historic tram tour y el Sintra day trip.",
])
def test_negated_conflict_is_not_a_conflict(text):
    assert flags_conflict(text, CONFLICT_TITLES) is False


@pytest.mark.parametrize("text", [
    "No worries about the budget, but the Historic tram tour overlaps with the Sintra day trip.",
    "There is no problem with the hotel; however, the tram tour and the Sintra day trip clash.",
])
def test_conflict_after_an_unrelated_negation_is_still_a_conflict(text):
    assert flags_conflict(text, CONFLICT_TITLES) is True


@pytest.mark.parametrize("text", [
    # "No problems with your hotel" (the exact phrase), with the trip's nights so that only
    # the gap part of the check decides.
    "No problems with your hotel: it covers your 4 nights from June 10 to 14.",
    "No issues with the accommodation for your 4 nights.",
    "There is nothing wrong with your hotel for the 4 nights.",
])
def test_no_and_hotel_in_one_sentence_is_not_a_gap(text):
    assert flags_missing_accommodation(text, START, END) is False


@pytest.mark.parametrize("text", [
    "You haven't booked any accommodation for your 4 nights yet.",
    "Accommodation is still missing for the 4 nights.",
    "Todavía no tienen alojamiento para las cuatro noches.",
])
def test_other_ways_of_saying_accommodation_is_missing(text):
    assert flags_missing_accommodation(text, START, END) is True


@pytest.mark.parametrize("text,claims", [
    ("Your hotel is now booked.", True),
    ("The Alfama apartment is reserved for you.", True),
    ("The hotel has been booked for June 10 to 14.", True),
    ("El hotel ya está reservado.", True),
    ("El apartamento ha sido reservado para ustedes.", True),
    ("La habitación quedó reservada.", True),
    # Negated or hypothetical passives are not claims.
    ("The hotel is not booked yet.", False),
    ("Nothing is booked until an editor confirms it in the app.", False),
    ("Once your hotel is booked, the price is fixed.", False),
    ("It can be reserved by an editor in the app.", False),
    ("El hotel no está reservado todavía.", False),
    ("Cuando el hotel esté reservado, el precio queda fijo.", False),
])
def test_passive_booking_claims(text, claims):
    assert claims_a_booking(text) is claims
