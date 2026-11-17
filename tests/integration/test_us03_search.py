"""US-03 — Search and compare travel options, against the provider mock over real HTTP.

The tests drive failure scenarios through the mock's /_control endpoints; the application
itself only ever talks to the public /v1 contract.
"""

import asyncio
import socket
import time
from datetime import date, datetime

import pytest

LISBON_STAY = {"category": "accommodation", "destination": "Lisbon"}
# Fields that would suggest a search offer is a booking or a guaranteed price.
FORBIDDEN_BOOKING_KEYS = {"status", "bookingId", "confirmed", "guaranteed", "booked"}


@pytest.fixture
async def trip(api, trip_payload):
    """Ana organizes a 2-traveler trip (2030-06-10..22); Beto edits; Carla views."""
    ana = api.as_("ana")
    created = (await ana.post("/trips", json=trip_payload)).json()
    for user, role in (("beto", "editor"), ("carla", "viewer")):
        await ana.put(f"/trips/{created['id']}/members/{user}", json={"role": role})
    return created


async def search(client, trip, body):
    return await client.post(f"/trips/{trip['id']}/offers/search", json=body)


async def set_scenario(mock_control, supplier, mode, **extra):
    r = await mock_control.post("/scenario", json={"supplierId": supplier, "mode": mode, **extra})
    r.raise_for_status()


# --- Complete results with everything needed to compare ---------------------------


async def test_complete_search_shows_price_schedule_availability_and_restrictions(
    api, trip, mock_control
):
    r = await search(api.as_("carla"), trip, LISBON_STAY)  # a viewer may search
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "complete"
    assert data["failedSuppliers"] == [] and data["error"] is None
    assert data["query"] == {
        "category": "accommodation", "destination": "Lisbon", "origin": None,
        "startsOn": "2030-06-10", "endsOn": "2030-06-22", "travelers": 2, "providerId": None,
    }
    assert {o["providerId"] for o in data["offers"]} == {"supplier-a", "supplier-b"}
    hotel = next(o for o in data["offers"] if o["offerId"] == "offer-lis-hotel-central-p2")
    assert hotel["price"] == {"amount": "670.00", "currency": "USD"}
    assert hotel["startsAt"] == "2030-06-10T15:00:00+01:00"  # provider-local schedule kept
    assert hotel["endsAt"] == "2030-06-14T11:00:00+01:00"
    assert hotel["available"] is True
    assert hotel["restrictions"] == ["Non-refundable simulated booking"]
    assert hotel["travelers"] == 2
    belem = next(o for o in data["offers"] if o["offerId"] == "offer-lis-hotel-belem-p2")
    assert belem["available"] is False


async def test_results_fit_the_trip_party_and_dates(api, trip, mock_control):
    data = (await search(api.as_("ana"), trip, LISBON_STAY)).json()
    for offer in data["offers"]:
        assert offer["travelers"] == trip["travelers"]
        assert date(2030, 6, 10) <= datetime.fromisoformat(offer["startsAt"]).date()
        assert datetime.fromisoformat(offer["endsAt"]).date() <= date(2030, 6, 22)


async def test_offers_are_presented_as_estimates_not_bookings(api, trip, mock_control):
    data = (await search(api.as_("ana"), trip, LISBON_STAY)).json()
    assert "not bookings" in data["disclaimer"] and "estimates" in data["disclaimer"]
    for offer in data["offers"]:
        assert offer["priceType"] == "estimate"
        assert not FORBIDDEN_BOOKING_KEYS & offer.keys()
    assert not FORBIDDEN_BOOKING_KEYS & data.keys()


async def test_freshness_is_explicit(api, trip, mock_control):
    data = (await search(api.as_("ana"), trip, LISBON_STAY)).json()
    searched = datetime.fromisoformat(data["searchedAt"])
    received = datetime.fromisoformat(data["receivedAt"])
    assert abs((received - searched).total_seconds()) < 60
    for offer in data["offers"]:
        assert datetime.fromisoformat(offer["validUntil"]) > searched
        assert offer["quoteExpired"] is False


async def test_expired_quotes_are_flagged(api, trip, mock_control):
    # The provider's clock says the quotes were issued long ago.
    await mock_control.post("/clock", json={"now": "2026-01-01T00:00:00Z"})
    data = (await search(api.as_("ana"), trip, LISBON_STAY)).json()
    assert data["offers"] and all(o["quoteExpired"] for o in data["offers"])


async def test_explicit_window_and_transport_search(api, trip, mock_control):
    r = await search(api.as_("beto"), trip, {
        "category": "transport", "destination": "Madrid", "origin": "lisbon",
        "startsOn": "2030-06-14", "endsOn": "2030-06-14",
    })
    data = r.json()
    assert r.status_code == 200 and data["outcome"] == "complete"
    assert {o["origin"] for o in data["offers"]} == {"Lisbon"}
    assert all(o["startsAt"].startswith("2030-06-14") for o in data["offers"])


# --- Partial results remain useful --------------------------------------------------


@pytest.mark.parametrize("mode,code", [
    ("unavailable", "PROVIDER_UNAVAILABLE"),
    ("rate_limited", "RATE_LIMITED"),
    ("timeout", "PROVIDER_TIMEOUT"),  # the mock gives up on the supplier after 1s
])
async def test_partial_search_names_failed_suppliers_and_keeps_others(
    api, trip, mock_control, mode, code
):
    await set_scenario(mock_control, "supplier-b", mode)
    r = await search(api.as_("carla"), trip, LISBON_STAY)
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "partial"
    assert "incomplete" in data["message"]
    assert [(f["providerId"], f["code"]) for f in data["failedSuppliers"]] == [
        ("supplier-b", code)]
    assert data["failedSuppliers"][0]["message"]
    assert data["offers"] and {o["providerId"] for o in data["offers"]} == {"supplier-a"}


# --- Empty --------------------------------------------------------------------------


async def test_empty_search_is_complete_with_no_offers(api, trip, mock_control):
    r = await search(api.as_("ana"), trip, {**LISBON_STAY, "startsOn": "2030-06-20"})
    data = r.json()
    assert r.status_code == 200
    assert data["outcome"] == "empty" and data["offers"] == []
    assert data["failedSuppliers"] == [] and data["error"] is None
    assert data["searchedAt"] is not None


# --- Failed ---------------------------------------------------------------------------


async def test_all_suppliers_failing_is_a_failed_search(api, trip, mock_control):
    await set_scenario(mock_control, "supplier-a", "unavailable")
    await set_scenario(mock_control, "supplier-b", "unavailable")
    r = await search(api.as_("ana"), trip, LISBON_STAY)
    assert r.status_code == 503
    data = r.json()
    assert data["outcome"] == "failed" and data["offers"] == []
    assert data["error"]["code"] == "PROVIDER_UNAVAILABLE"
    assert data["error"]["providerRequestId"]
    assert {f["providerId"] for f in data["failedSuppliers"]} == {"supplier-a", "supplier-b"}
    assert data["searchedAt"] is None and data["requestId"] == r.headers["X-Request-Id"]


async def test_rate_limited_search_says_when_to_retry(api, trip, mock_control):
    await set_scenario(mock_control, "supplier-b", "rate_limited", retryAfterSeconds=7)
    r = await search(api.as_("ana"), trip, {**LISBON_STAY, "providerId": "supplier-b"})
    assert r.status_code == 503
    data = r.json()
    assert data["outcome"] == "failed"
    assert data["error"]["code"] == "PROVIDER_RATE_LIMITED"
    assert data["error"]["retryAfterSeconds"] == 7
    assert r.headers["Retry-After"] == "7"


async def test_slow_provider_fails_within_the_timeout(api, make_app, trip, mock_control):
    # The mock holds a timing-out supplier for 1s; the application only waits 0.3s.
    await set_scenario(mock_control, "supplier-b", "timeout")
    impatient = api.as_("ana", app=make_app(provider_timeout_seconds=0.3))
    started = time.monotonic()
    r = await search(impatient, trip, LISBON_STAY)
    elapsed = time.monotonic() - started
    assert r.status_code == 504
    data = r.json()
    assert data["outcome"] == "failed" and data["error"]["code"] == "PROVIDER_TIMEOUT"
    assert "0.3 seconds" in data["error"]["message"]
    assert elapsed < 0.9, f"took {elapsed:.2f}s"


async def test_unreachable_provider_fails_fast_and_clearly(api, make_app, trip):
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    down = make_app(provider_base_url=f"http://127.0.0.1:{port}/v1")
    started = time.monotonic()
    r = await search(api.as_("ana", app=down), trip, LISBON_STAY)
    assert r.status_code == 503 and time.monotonic() - started < 3
    assert r.json()["outcome"] == "failed"
    assert r.json()["error"]["code"] == "PROVIDER_UNREACHABLE"


async def test_wrong_provider_credentials_are_reported_as_a_failed_search(
    api, make_app, trip, mock_control
):
    misconfigured = make_app(provider_token="wrong-token")
    r = await search(api.as_("ana", app=misconfigured), trip, LISBON_STAY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_AUTH_FAILED"


async def test_concurrent_searches_do_not_wait_on_each_other(api, trip, mock_control):
    await set_scenario(mock_control, "supplier-b", "timeout")  # each search takes ~1s
    started = time.monotonic()
    results = await asyncio.gather(*(search(api.as_("ana"), trip, LISBON_STAY)
                                     for _ in range(5)))
    assert all(r.json()["outcome"] == "partial" for r in results)
    assert time.monotonic() - started < 3


# --- Input validation ---------------------------------------------------------------


@pytest.mark.parametrize("body,field", [
    ({"category": "transport", "destination": "Lisbon"}, "origin"),
    ({"category": "cruise", "destination": "Lisbon"}, "category"),
    ({"category": "activity", "destination": " "}, "destination"),
    ({**LISBON_STAY, "startsOn": "2030-06-01"}, "startsOn"),
    ({**LISBON_STAY, "endsOn": "2030-07-01"}, "endsOn"),
    ({**LISBON_STAY, "startsOn": "2030-06-15", "endsOn": "2030-06-12"}, "endsOn"),
    ({**LISBON_STAY, "travelers": 3}, "travelers"),  # the party comes from the trip
    ({**LISBON_STAY, "providerId": "supplier-z"}, "query"),  # rejected by the provider
])
async def test_invalid_searches_get_field_feedback(api, trip, mock_control, body, field):
    r = await search(api.as_("ana"), trip, body)
    assert r.status_code == 422
    assert field in [f["field"] for f in r.json()["details"]["fields"]]


# --- Permissions ----------------------------------------------------------------------


@pytest.mark.parametrize("user", ["ana", "beto", "carla"])
async def test_every_participant_can_search(api, trip, mock_control, user):
    r = await search(api.as_(user), trip, LISBON_STAY)
    assert r.status_code == 200 and r.json()["outcome"] == "complete"


async def test_nonparticipant_gets_404_and_provider_is_not_called(api, trip, mock_control):
    # If the provider were called, this scenario would turn the answer into a 503.
    await set_scenario(mock_control, "supplier-a", "unavailable")
    await set_scenario(mock_control, "supplier-b", "unavailable")
    r = await search(api.as_("dani"), trip, LISBON_STAY)
    assert r.status_code == 404 and r.json()["code"] == "TRIP_NOT_FOUND"


async def test_unauthenticated_search_is_401(api, trip):
    r = await search(api.as_(None), trip, LISBON_STAY)
    assert r.status_code == 401
