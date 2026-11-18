"""The US-11 model-evaluation checks, run end to end with a scripted model: the chat endpoint,
the loop and the MCP tools are real, only the model's words are fixed. A good answer passes
each check and a deficient one fails it, so the checks used against Gemini can detect the
behaviour they claim to measure (no model access needed)."""

from datetime import date

import pytest

from app.adapters.scripted_model import ScriptedModel, call, say
from evals.support import (
    check_points_out_conflict,
    check_points_out_missing_accommodation,
    check_relates_to_dates_and_restrictions,
)

LISBON = {"name": "Lisbon", "destinations": ["Lisbon"], "startsOn": "2030-06-10",
          "endsOn": "2030-06-14", "budget": "3000.00"}
SEARCH = {"category": "accommodation", "destination": "Lisbon",
          "starts_on": "2030-06-10", "ends_on": "2030-06-14"}
START, END = date(2030, 6, 10), date(2030, 6, 14)
CONFLICT = ["Historic tram tour", "Sintra day trip"]


async def lisbon_trip(api, trip_payload):
    return (await api.as_("ana").post("/trips", json={**trip_payload, **LISBON})).json()


async def chat_with(api, make_app, trip, steps, message):
    app = make_app(chat_model=ScriptedModel(steps))
    r = await api.as_("ana", app=app).post(f"/trips/{trip['id']}/assistant/chat",
                                           json={"message": message})
    assert r.status_code == 200, r.text
    return r.json()


GOOD_STAYS = ("For your 4 nights (June 10 to 14): the Alfama apartment at 600.00 USD leaves "
              "2400.00 USD of budget, but there is no check-in after 22:00. The Baixa hostel "
              "beds cost 225.60 USD and have a shared bathroom. Prices are quotes, not "
              "bookings; I cannot book for you.")


@pytest.mark.parametrize("answer,passes", [
    (GOOD_STAYS, True),
    # No dates or nights at all.
    ("The Alfama apartment costs 600.00 USD, but there is no check-in after 22:00.", False),
    # Proposes the hostel without its restriction.
    ("For your 4 nights, the Baixa hostel beds cost 225.60 USD.", False),
    # Proposes nothing that was retrieved.
    ("For your 4 nights, I suggest a nice place near the river.", False),
])
async def test_dates_and_restrictions_check(api, make_app, trip_payload, mock_control,
                                            answer, passes):
    trip = await lisbon_trip(api, trip_payload)
    body = await chat_with(api, make_app, trip,
                           [call("search_offers", trip_id=trip["id"], **SEARCH), say(answer)],
                           "Find us accommodation in Lisbon for our trip.")
    if passes:
        check_relates_to_dates_and_restrictions(body, START, END)
    else:
        with pytest.raises(AssertionError):
            check_relates_to_dates_and_restrictions(body, START, END)


@pytest.mark.parametrize("answer,passes", [
    ("Your Historic tram tour overlaps with the Sintra day trip on June 11; consider moving "
     "one of them in the app.", True),
    ("Everything looks fine: the tram tour and the Sintra day trip are both booked in.", False),
    ("I removed the tram tour because it overlapped with the Sintra day trip.", False),
])
async def test_conflict_check(api, make_app, trip_payload, mock_control, answer, passes):
    trip = await lisbon_trip(api, trip_payload)
    ana = api.as_("ana")
    for version, offer in ((1, "offer-lis-tram-tour-p2"), (2, "offer-lis-sintra-day-p2")):
        r = await ana.post(f"/trips/{trip['id']}/itinerary/items",
                           json={"version": version, "offerId": offer})
        assert r.status_code == 201, r.text
    body = await chat_with(api, make_app, trip,
                           [call("get_itinerary", trip_id=trip["id"]), say(answer)],
                           "Can you review our itinerary and tell us if anything needs attention?")
    # The scripted model sees the real conflict flag in the tool result it received.
    assert "SCHEDULE_CONFLICT" in str(body["toolCalls"][0]["result"])
    if passes:
        check_points_out_conflict(body, CONFLICT)
    else:
        with pytest.raises(AssertionError):
            check_points_out_conflict(body, CONFLICT)


async def test_conflict_check_requires_reading_the_itinerary(api, make_app, trip_payload,
                                                              mock_control):
    trip = await lisbon_trip(api, trip_payload)
    body = await chat_with(api, make_app, trip,
                           [say("Your Historic tram tour overlaps with the Sintra day trip.")],
                           "Anything wrong with our plan?")
    with pytest.raises(AssertionError, match="itinerary"):
        check_points_out_conflict(body, CONFLICT)


@pytest.mark.parametrize("answer,passes", [
    ("Your Historic tram tour overlaps with the Sintra day trip. Also, you have no "
     "accommodation for your 4 nights in Lisbon (June 10 to 14).", True),
    ("Your Historic tram tour overlaps with the Sintra day trip.", False),  # gap not mentioned
    ("You have no accommodation yet.", False),  # not tied to the nights or dates
    # Claims to have changed the itinerary.
    ("I added a hotel for your 4 nights, since you had no accommodation.", False),
])
async def test_missing_accommodation_check(api, make_app, trip_payload, mock_control, answer,
                                           passes):
    trip = await lisbon_trip(api, trip_payload)
    ana = api.as_("ana")
    for version, offer in ((1, "offer-lis-tram-tour-p2"), (2, "offer-lis-sintra-day-p2")):
        r = await ana.post(f"/trips/{trip['id']}/itinerary/items",
                           json={"version": version, "offerId": offer})
        assert r.status_code == 201, r.text
    body = await chat_with(api, make_app, trip,
                           [call("get_itinerary", trip_id=trip["id"]), say(answer)],
                           "Can you review our itinerary and tell us if anything needs attention?")
    # The scripted model received the real gap computed by the application.
    assert "NIGHTS_WITHOUT_ACCOMMODATION" in str(body["toolCalls"][0]["result"])
    if passes:
        check_points_out_missing_accommodation(body, START, END)
    else:
        with pytest.raises(AssertionError):
            check_points_out_missing_accommodation(body, START, END)
