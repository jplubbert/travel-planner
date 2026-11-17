"""US-01 — Create and return to a trip (plus ADR 0001 for edits)."""

import asyncio

import pytest


async def create(client, payload):
    r = await client.post("/trips", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# --- A trip records destinations, dates and a USD budget ------------------------


async def test_organizer_creates_trip_with_destinations_dates_and_budget(api, trip_payload):
    r = await api.as_("ana").post("/trips", json=trip_payload)
    assert r.status_code == 201
    trip = r.json()
    assert trip["name"] == "Iberia + Paris"
    assert trip["destinations"] == ["Lisbon", "Madrid", "Paris"]  # order preserved
    assert (trip["startsOn"], trip["endsOn"]) == ("2030-06-10", "2030-06-22")
    assert trip["travelers"] == 2
    assert trip["budget"] == {"amount": "4500.00", "currency": "USD"}
    assert trip["version"] == 1 and trip["myRole"] == "organizer"
    assert trip["createdBy"] == "ana"
    assert r.headers["Location"] == f"/trips/{trip['id']}"
    assert r.headers["ETag"] == '"1"'


async def test_single_destination_and_budget_normalization(api, trip_payload):
    trip = await create(api.as_("ana"), {**trip_payload, "destinations": ["  Lisbon "],
                                         "budget": "1500.5", "name": "  Lisbon  "})
    assert trip["destinations"] == ["Lisbon"] and trip["name"] == "Lisbon"
    assert trip["budget"]["amount"] == "1500.50"


# --- Returning in another session -------------------------------------------------


async def test_organizer_finds_saved_trip_in_another_session(api, trip_payload):
    created = await create(api.as_("ana"), trip_payload)

    later = api.as_("ana")  # a new client: no shared state with the first one
    listed = (await later.get("/trips")).json()
    assert [t["id"] for t in listed] == [created["id"]]
    r = await later.get(f"/trips/{created['id']}")
    assert r.status_code == 200 and r.json() == created


async def test_trips_survive_application_restart(api, make_app, trip_payload):
    created = await create(api.as_("ana"), trip_payload)
    await api.app.state.shutdown()  # the first instance shuts down

    restarted = make_app()
    r = await api.as_("ana", app=restarted).get(f"/trips/{created['id']}")
    assert r.status_code == 200 and r.json() == created


# --- Invalid input gives actionable feedback and saves nothing ----------------------


@pytest.mark.parametrize("change,field", [
    ({"name": "   "}, "name"),
    ({"destinations": []}, "destinations"),
    ({"destinations": ["Lisbon", ""]}, "destinations[1]"),
    ({"destinations": ["Lisbon", "lisbon"]}, "destinations[1]"),
    ({"endsOn": "2030-06-09"}, "endsOn"),
    ({"startsOn": "2020-01-01", "endsOn": "2020-01-05"}, "startsOn"),
    ({"startsOn": "2030-02-30"}, "startsOn"),
    ({"startsOn": "next week"}, "startsOn"),
    ({"travelers": 0}, "travelers"),
    ({"travelers": 21}, "travelers"),
    ({"travelers": "2"}, "travelers"),
    ({"budget": "-1.00"}, "budget"),
    ({"budget": "12.345"}, "budget"),
    ({"budget": 4500}, "budget"),
    ({"budget": "lots"}, "budget"),
    ({"currency": "EUR"}, "currency"),
])
async def test_invalid_input_is_rejected_with_field_details(api, trip_payload, change, field):
    client = api.as_("ana")
    r = await client.post("/trips", json={**trip_payload, **change})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR" and body["requestId"]
    assert field in [f["field"] for f in body["details"]["fields"]], body
    assert all(f["message"] for f in body["details"]["fields"])
    assert (await client.get("/trips")).json() == []  # nothing was saved


@pytest.mark.parametrize("missing", ["name", "destinations", "startsOn", "endsOn",
                                     "travelers", "budget"])
async def test_missing_fields_are_reported(api, trip_payload, missing):
    payload = {k: v for k, v in trip_payload.items() if k != missing}
    r = await api.as_("ana").post("/trips", json=payload)
    assert r.status_code == 422
    assert missing in [f["field"] for f in r.json()["details"]["fields"]]


async def test_all_domain_problems_are_reported_at_once(api, trip_payload):
    r = await api.as_("ana").post("/trips", json={
        **trip_payload, "name": "", "endsOn": "2030-06-01", "budget": "-5"})
    fields = {f["field"] for f in r.json()["details"]["fields"]}
    assert {"name", "endsOn", "budget"} <= fields


# --- Identity -----------------------------------------------------------------------


@pytest.mark.parametrize("user", [None, "mallory"])
async def test_requests_without_a_known_user_are_unauthenticated(api, trip_payload, user):
    client = api.as_(user)
    for r in (await client.post("/trips", json=trip_payload), await client.get("/trips")):
        assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


# --- Editing with optimistic concurrency (ADR 0001) ----------------------------------


async def test_update_with_current_version_saves_and_increments(api, trip_payload):
    client = api.as_("ana")
    trip = await create(client, trip_payload)
    r = await client.patch(f"/trips/{trip['id']}", json={"version": 1, "budget": "5000"})
    assert r.status_code == 200
    updated = r.json()
    assert updated["budget"]["amount"] == "5000.00" and updated["version"] == 2
    assert updated["destinations"] == trip["destinations"]  # untouched fields kept
    assert (await client.get(f"/trips/{trip['id']}")).json() == updated


async def test_update_without_version_is_refused(api, trip_payload):
    client = api.as_("ana")
    trip = await create(client, trip_payload)
    r = await client.patch(f"/trips/{trip['id']}", json={"name": "New"})
    assert r.status_code == 400 and r.json()["code"] == "VERSION_REQUIRED"
    assert (await client.get(f"/trips/{trip['id']}")).json()["name"] == trip["name"]


async def test_stale_version_gets_409_with_current_state(api, trip_payload):
    client = api.as_("ana")
    trip = await create(client, trip_payload)
    await client.patch(f"/trips/{trip['id']}", json={"version": 1, "name": "First"})
    r = await client.patch(f"/trips/{trip['id']}", json={"version": 1, "name": "Second"})
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "TRIP_VERSION_CONFLICT"
    assert body["details"]["current"]["name"] == "First"
    assert body["details"]["current"]["version"] == 2


async def test_concurrent_updates_never_silently_overwrite(api, trip_payload):
    organizer = api.as_("ana")
    trip = await create(organizer, trip_payload)
    await organizer.put(f"/trips/{trip['id']}/members/beto", json={"role": "editor"})
    editor = api.as_("beto")

    names = [f"Edit {i}" for i in range(8)]
    responses = await asyncio.gather(*(
        (organizer if i % 2 else editor).patch(
            f"/trips/{trip['id']}", json={"version": 1, "name": name})
        for i, name in enumerate(names)
    ))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] + [409] * 7
    winner = next(r.json() for r in responses if r.status_code == 200)
    saved = (await organizer.get(f"/trips/{trip['id']}")).json()
    assert saved["name"] == winner["name"] and saved["version"] == 2


async def test_invalid_update_keeps_trip_unchanged(api, trip_payload):
    client = api.as_("ana")
    trip = await create(client, trip_payload)
    r = await client.patch(f"/trips/{trip['id']}", json={"version": 1, "endsOn": "2030-06-01"})
    assert r.status_code == 422
    assert (await client.get(f"/trips/{trip['id']}")).json() == trip


async def test_past_start_date_only_checked_when_dates_change(api, trip_payload):
    client = api.as_("ana")
    trip = await create(client, trip_payload)
    r = await client.patch(f"/trips/{trip['id']}",
                           json={"version": 1, "startsOn": "2020-01-01"})
    assert r.status_code == 422
    r = await client.patch(f"/trips/{trip['id']}",
                           json={"version": 1, "startsOn": trip["startsOn"], "name": "Same"})
    assert r.status_code == 200
