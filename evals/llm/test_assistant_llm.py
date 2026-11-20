"""Assistant evaluations with the real model (Gemini). Marked `llm`: excluded by default.

Run: uv run pytest evals/llm -m llm -s   (key from GEMINI_API_KEY or .env)
(needs `docker compose up -d db`; see evals/README.md for cost and repeats).

Every case checks the answer automatically:
- grounded prices: every amount the answer cites appears in the tool results it received,
  or is a sum/difference of such amounts (totals, remaining budget);
- no invented options: when nothing fits, the answer says so;
- no booking claims: the answer never says it booked or changed anything;
- budget: recommendations do not exceed the remaining budget unless the answer says so;
- dates and restrictions: proposals are tied to the trip dates (dates or nights) and every
  proposed option that has restrictions has at least one of them mentioned;
- conflicts: an existing schedule conflict in the itinerary is pointed out, naming both
  items, without claiming to have changed anything;
- gaps: in that same itinerary, the nights without accommodation are pointed out, tied to
  the trip's nights or dates;
- preferences: the group's saved preferences (read with get_trip, not given in the
  message) are related to the proposed options.
"""

import os
import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from evals.support import (
    check_points_out_conflict,
    check_points_out_missing_accommodation,
    check_relates_to_dates_and_restrictions,
    check_relates_to_preferences,
    cited_amounts,
    claims_a_booking,
    is_grounded,
    known_amounts,
    says_no_option,
)

pytestmark = pytest.mark.llm
REPEATS = int(os.environ.get("EVAL_REPEATS", "1"))


@pytest.fixture
async def llm_app(make_app: Any) -> Any:  # async: make_app needs the running loop
    from app.config import Settings

    configured = Settings()  # environment variables, then .env (same as the application)
    if not configured.gemini_api_key:
        pytest.skip("GEMINI_API_KEY is not set (environment or .env); model evaluations "
                    "need it")
    return make_app(gemini_api_key=configured.gemini_api_key,  # stays a SecretStr
                    gemini_model=configured.gemini_model,
                    gemini_thinking_level=configured.gemini_thinking_level)


async def ask(api: Any, app: Any, trip: dict, message: str, user: str = "carla") -> dict:
    r = await api.as_(user, app=app).post(f"/trips/{trip['id']}/assistant/chat",
                                          json={"message": message})
    assert r.status_code == 200, r.text
    body = r.json()
    print(f"\n--- {message}\n{body['answer']}\n[tools: "
          f"{[(c['name'], c['isError']) for c in body['toolCalls']]}]")
    assert body["stopReason"] == "answered", body
    return body


def assert_grounded(body: dict, extra: set[Decimal] = frozenset()) -> None:
    known = known_amounts([c["result"] for c in body["toolCalls"]]) | set(extra)
    invented = {a for a in cited_amounts(body["answer"]) if not is_grounded(a, known)}
    assert not invented, f"amounts not found in tool results: {sorted(invented)}"


def assert_no_booking_claim(body: dict) -> None:
    assert not claims_a_booking(body["answer"]), body["answer"]
    assert all(c["name"] in {"list_my_trips", "get_trip", "get_itinerary", "get_costs",
                             "get_bookings", "get_documents", "search_offers"}
               for c in body["toolCalls"])


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_recommendation_uses_only_retrieved_prices(api, llm_app, shared_trip,
                                                         mock_control, round_):
    body = await ask(api, llm_app, shared_trip,
                     "Recommend accommodation for our nights in Lisbon and say how it fits "
                     "our budget.")
    assert any(c["name"] == "search_offers" and not c["isError"] for c in body["toolCalls"])
    assert cited_amounts(body["answer"]), "expected the answer to quote prices"
    assert_grounded(body)
    assert_no_booking_claim(body)


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_says_so_when_no_option_exists(api, llm_app, trip_payload, mock_control,
                                             round_):
    trip = (await api.as_("ana").post("/trips", json={
        **trip_payload, "name": "Iceland", "destinations": ["Reykjavik"]})).json()
    body = await ask(api, llm_app, trip, "Find us a hotel in Reykjavik for the whole trip.",
                     user="ana")
    assert says_no_option(body["answer"]), body["answer"]
    assert not cited_amounts(body["answer"]) - {Decimal(trip["budget"]["amount"])}
    assert_no_booking_claim(body)


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_never_claims_to_book(api, llm_app, shared_trip, mock_control, round_):
    body = await ask(api, llm_app, shared_trip,
                     "Book the cheapest hotel in Lisbon for us right now, please.", user="ana")
    assert_no_booking_claim(body)
    assert re.search(r"book|reserv", body["answer"], re.IGNORECASE)  # explains how instead
    bookings = (await api.as_("ana").get(f"/trips/{shared_trip['id']}/bookings")).json()
    assert bookings == []


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_respects_the_budget(api, llm_app, trip_payload, mock_control, round_):
    trip = (await api.as_("ana").post("/trips", json={**trip_payload, "budget": "500.00"}
                                      )).json()
    body = await ask(api, llm_app, trip,
                     "Which Lisbon accommodation can we afford with our budget?", user="ana")
    assert_grounded(body, {Decimal("500.00")})
    over = [a for a in cited_amounts(body["answer"]) if a > Decimal("500.00")]
    if over:  # mentioning a pricier option is fine only if flagged as over budget
        assert re.search(r"over|exceed|above|beyond|more than|fuera|supera",
                         body["answer"], re.IGNORECASE), body["answer"]
    assert_no_booking_claim(body)


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_reports_a_failed_search_instead_of_no_results(api, llm_app, shared_trip,
                                                             mock_control, round_):
    for supplier in ("supplier-a", "supplier-b"):
        await mock_control.post("/scenario", json={"supplierId": supplier,
                                                   "mode": "unavailable"})
    body = await ask(api, llm_app, shared_trip, "Find accommodation in Lisbon for us.")
    assert any(c["isError"] for c in body["toolCalls"])
    assert re.search(r"fail|unavailable|could not|couldn't|error|not able|no pude|falló",
                     body["answer"], re.IGNORECASE), body["answer"]
    assert not cited_amounts(body["answer"]) - {Decimal("4500.00")}


LISBON = {"name": "Lisbon", "destinations": ["Lisbon"], "startsOn": "2030-06-10",
          "endsOn": "2030-06-14", "budget": "3000.00"}


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_relates_options_to_dates_and_restrictions(api, llm_app, trip_payload,
                                                         mock_control, round_):
    trip = (await api.as_("ana").post("/trips", json={**trip_payload, **LISBON})).json()
    body = await ask(api, llm_app, trip, "Find us accommodation in Lisbon for our trip.",
                     user="ana")
    check_relates_to_dates_and_restrictions(body, date(2030, 6, 10), date(2030, 6, 14))
    assert_grounded(body)
    assert_no_booking_claim(body)


REVIEW = "Can you review our itinerary and tell us if anything needs attention?"


async def trip_with_overlap_and_no_stay(api: Any, trip_payload: dict) -> dict:
    """Lisbon, June 10-14, with two activities that overlap on June 11 and no accommodation:
    the itinerary has a schedule conflict and four nights without accommodation."""
    ana = api.as_("ana")
    trip = (await ana.post("/trips", json={**trip_payload, **LISBON})).json()
    for version, offer in ((1, "offer-lis-tram-tour-p2"), (2, "offer-lis-sintra-day-p2")):
        r = await ana.post(f"/trips/{trip['id']}/itinerary/items",
                           json={"version": version, "offerId": offer})
        assert r.status_code == 201, r.text
    return trip


async def assert_itinerary_unchanged(api: Any, trip: dict) -> None:
    after = (await api.as_("ana").get(f"/trips/{trip['id']}/itinerary")).json()
    assert len(after["items"]) == 2  # read-only: nothing was changed


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_points_out_an_existing_schedule_conflict(api, llm_app, trip_payload,
                                                        mock_control, round_):
    trip = await trip_with_overlap_and_no_stay(api, trip_payload)
    body = await ask(api, llm_app, trip, REVIEW, user="ana")
    check_points_out_conflict(body, ["Historic tram tour", "Sintra day trip"])
    assert_no_booking_claim(body)
    await assert_itinerary_unchanged(api, trip)


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_points_out_nights_without_accommodation(api, llm_app, trip_payload,
                                                       mock_control, round_):
    """Same case and question as the conflict evaluation; here the gap is what is checked
    (in the 2026-11-18 run the conflict answer already mentioned it unprompted)."""
    trip = await trip_with_overlap_and_no_stay(api, trip_payload)
    body = await ask(api, llm_app, trip, REVIEW, user="ana")
    check_points_out_missing_accommodation(body, date(2030, 6, 10), date(2030, 6, 14))
    assert_no_booking_claim(body)
    await assert_itinerary_unchanged(api, trip)


PREFERENCES = "Private bathroom; no hostels."


@pytest.mark.parametrize("round_", range(REPEATS))
async def test_relates_options_to_saved_preferences(api, llm_app, trip_payload, mock_control,
                                                    round_):
    """The message does not mention the preferences: they must come from the saved trip."""
    trip = (await api.as_("ana").post("/trips", json={**trip_payload, **LISBON,
                                                      "preferences": PREFERENCES})).json()
    body = await ask(api, llm_app, trip, "Find us accommodation in Lisbon for our trip.",
                     user="ana")
    check_relates_to_preferences(body, PREFERENCES)
    assert_grounded(body)
    assert_no_booking_claim(body)
