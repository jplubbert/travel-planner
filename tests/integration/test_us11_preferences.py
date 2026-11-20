"""US-11 — The group's saved preferences and restrictions, as part of the trip.

Created and edited like the other trip details (PATCH with version, ADR 0001), so they bump
the version, appear in the change history and are validated per field."""

import pytest

PREFS = "Private bathroom; no overnight journeys."


async def create(client, payload):
    r = await client.post("/trips", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def test_preferences_are_optional_and_default_to_empty(api, trip_payload):
    trip = await create(api.as_("ana"), trip_payload)
    assert trip["preferences"] == ""


async def test_trip_is_created_with_preferences(api, trip_payload):
    client = api.as_("ana")
    trip = await create(client, {**trip_payload, "preferences": f"  {PREFS}  "})
    assert trip["preferences"] == PREFS  # trimmed like the name
    assert (await client.get(f"/trips/{trip['id']}")).json()["preferences"] == PREFS
    assert (await client.get("/trips")).json()[0]["preferences"] == PREFS


async def test_editor_edits_preferences_with_the_version(api, shared_trip):
    url = f"/trips/{shared_trip['id']}"
    r = await api.as_("beto").patch(url, json={"version": 1, "preferences": PREFS})
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["preferences"] == PREFS and updated["version"] == 2
    assert updated["budget"] == shared_trip["budget"]  # untouched fields kept
    assert (await api.as_("carla").get(url)).json() == {**updated, "myRole": "viewer"}

    r = await api.as_("ana").patch(url, json={"version": 2, "preferences": ""})  # cleared
    assert r.status_code == 200 and r.json()["preferences"] == "" and r.json()["version"] == 3


async def test_stale_version_gets_409_with_the_preferences_change_in_history(api, shared_trip):
    url = f"/trips/{shared_trip['id']}"
    assert (await api.as_("beto").patch(
        url, json={"version": 1, "preferences": PREFS})).status_code == 200
    r = await api.as_("ana").patch(url, json={"version": 1, "preferences": "Hotels only"})
    assert r.status_code == 409
    details = r.json()["details"]
    assert details["current"]["preferences"] == PREFS and details["current"]["version"] == 2
    [change] = details["changesSinceYourVersion"]
    assert (change["version"], change["kind"], change["by"]) == (2, "trip_updated", "beto")
    assert change["summary"] == "Updated preferences"
    assert (await api.as_("ana").get(url)).json()["preferences"] == PREFS  # nothing lost


async def test_viewer_cannot_edit_preferences(api, shared_trip):
    r = await api.as_("carla").patch(f"/trips/{shared_trip['id']}",
                                     json={"version": 1, "preferences": PREFS})
    assert r.status_code == 403
    assert (await api.as_("ana").get(f"/trips/{shared_trip['id']}")).json() == shared_trip


async def test_500_characters_are_accepted(api, trip_payload):
    trip = await create(api.as_("ana"), {**trip_payload, "preferences": "x" * 500})
    assert len(trip["preferences"]) == 500


@pytest.mark.parametrize("value", ["x" * 501, 42, None, ["quiet"]])
async def test_invalid_preferences_on_create_are_reported_per_field(api, trip_payload, value):
    client = api.as_("ana")
    r = await client.post("/trips", json={**trip_payload, "preferences": value,
                                          "budget": "-1"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    fields = {f["field"]: f["message"] for f in body["details"]["fields"]}
    assert "preferences" in fields and fields["preferences"]
    assert (await client.get("/trips")).json() == []  # nothing was saved


async def test_too_long_preferences_report_the_limit_with_other_field_errors(
    api, trip_payload
):
    r = await api.as_("ana").post("/trips", json={**trip_payload, "preferences": "x" * 501,
                                                  "name": " "})
    fields = {f["field"]: f["message"] for f in r.json()["details"]["fields"]}
    assert fields == {"name": "is required",
                      "preferences": "must be at most 500 characters"}


async def test_invalid_preferences_on_edit_keep_the_trip_unchanged(api, shared_trip):
    url = f"/trips/{shared_trip['id']}"
    r = await api.as_("ana").patch(url, json={"version": 1, "preferences": "x" * 501})
    assert r.status_code == 422
    assert [f["field"] for f in r.json()["details"]["fields"]] == ["preferences"]
    assert (await api.as_("ana").get(url)).json() == shared_trip  # version still 1
