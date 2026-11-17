"""US-02 — Share a trip with appropriate access."""

import uuid

import pytest


@pytest.fixture
async def shared_trip(api, trip_payload):
    """Ana organizes; Beto edits; Carla views; Dani is not a participant."""
    ana = api.as_("ana")
    trip = (await ana.post("/trips", json=trip_payload)).json()
    for user, role in (("beto", "editor"), ("carla", "viewer")):
        r = await ana.put(f"/trips/{trip['id']}/members/{user}", json={"role": role})
        assert r.status_code == 201, r.text
    return trip


async def test_organizer_grants_view_and_edit_access(api, shared_trip):
    r = await api.as_("ana").get(f"/trips/{shared_trip['id']}/members")
    assert r.status_code == 200
    assert [(m["userId"], m["role"]) for m in r.json()] == [
        ("ana", "organizer"), ("beto", "editor"), ("carla", "viewer")]


@pytest.mark.parametrize("user,role", [("beto", "editor"), ("carla", "viewer")])
async def test_participants_read_the_trip_and_see_their_role(api, shared_trip, user, role):
    client = api.as_(user)
    r = await client.get(f"/trips/{shared_trip['id']}")
    assert r.status_code == 200 and r.json()["myRole"] == role
    assert r.json()["name"] == shared_trip["name"]
    assert (await client.get(f"/trips/{shared_trip['id']}/members")).status_code == 200
    assert [t["id"] for t in (await client.get("/trips")).json()] == [shared_trip["id"]]


async def test_editor_can_change_the_trip(api, shared_trip):
    r = await api.as_("beto").patch(
        f"/trips/{shared_trip['id']}", json={"version": 1, "budget": "5200.00"})
    assert r.status_code == 200 and r.json()["budget"]["amount"] == "5200.00"


async def test_viewer_cannot_change_the_trip(api, shared_trip):
    r = await api.as_("carla").patch(
        f"/trips/{shared_trip['id']}", json={"version": 1, "budget": "1.00"})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN"
    trip = (await api.as_("ana").get(f"/trips/{shared_trip['id']}")).json()
    assert trip["budget"]["amount"] == "4500.00" and trip["version"] == 1


async def test_viewer_without_version_still_gets_403_not_400(api, shared_trip):
    """Authorization is checked before the version (ADR 0001)."""
    r = await api.as_("carla").patch(f"/trips/{shared_trip['id']}", json={"name": "x"})
    assert r.status_code == 403


@pytest.mark.parametrize("user", ["beto", "carla"])
async def test_only_the_organizer_manages_participants(api, shared_trip, user):
    client = api.as_(user)
    r = await client.put(f"/trips/{shared_trip['id']}/members/dani", json={"role": "viewer"})
    assert r.status_code == 403
    r = await client.delete(f"/trips/{shared_trip['id']}/members/carla")
    assert r.status_code == 403
    members = (await api.as_("ana").get(f"/trips/{shared_trip['id']}/members")).json()
    assert {m["userId"] for m in members} == {"ana", "beto", "carla"}


# --- Nonparticipants: 404, indistinguishable from a missing trip ---------------------

NONPARTICIPANT_REQUESTS = [
    ("GET", "/trips/{id}", None),
    ("PATCH", "/trips/{id}", {"version": 1, "name": "hijack"}),
    ("GET", "/trips/{id}/members", None),
    ("PUT", "/trips/{id}/members/dani", {"role": "editor"}),
    ("DELETE", "/trips/{id}/members/carla", None),
]


@pytest.mark.parametrize("method,path,body", NONPARTICIPANT_REQUESTS)
async def test_nonparticipant_gets_404_for_every_trip_operation(
    api, shared_trip, method, path, body
):
    dani = api.as_("dani")
    r = await dani.request(method, path.format(id=shared_trip["id"]), json=body)
    assert r.status_code == 404
    missing = await dani.request(method, path.format(id=uuid.uuid4()), json=body)
    assert missing.status_code == 404
    # Same answer as for a trip that does not exist: no information leak.
    assert (r.json()["code"], r.json()["message"]) == (
        missing.json()["code"], missing.json()["message"]) == ("TRIP_NOT_FOUND", "Trip not found.")


async def test_nonparticipant_does_not_see_trip_in_list(api, shared_trip):
    assert (await api.as_("dani").get("/trips")).json() == []


async def test_nonparticipant_attempts_change_nothing(api, shared_trip):
    dani = api.as_("dani")
    for method, path, body in NONPARTICIPANT_REQUESTS:
        await dani.request(method, path.format(id=shared_trip["id"]), json=body)
    ana = api.as_("ana")
    assert (await ana.get(f"/trips/{shared_trip['id']}")).json()["name"] == shared_trip["name"]
    members = (await ana.get(f"/trips/{shared_trip['id']}/members")).json()
    assert {m["userId"]: m["role"] for m in members} == {
        "ana": "organizer", "beto": "editor", "carla": "viewer"}


async def test_malformed_trip_id_is_also_404(api):
    r = await api.as_("ana").get("/trips/not-a-uuid")
    assert r.status_code == 404 and r.json()["code"] == "TRIP_NOT_FOUND"


# --- Role changes take effect immediately ------------------------------------------


async def test_promoting_a_viewer_lets_them_edit(api, shared_trip):
    ana, carla = api.as_("ana"), api.as_("carla")
    r = await ana.put(f"/trips/{shared_trip['id']}/members/carla", json={"role": "editor"})
    assert r.status_code == 200 and r.json()["role"] == "editor"
    r = await carla.patch(f"/trips/{shared_trip['id']}", json={"version": 1, "name": "Carla's"})
    assert r.status_code == 200


async def test_demoting_an_editor_removes_edit_rights(api, shared_trip):
    await api.as_("ana").put(f"/trips/{shared_trip['id']}/members/beto", json={"role": "viewer"})
    r = await api.as_("beto").patch(f"/trips/{shared_trip['id']}", json={"version": 1, "name": "x"})
    assert r.status_code == 403


async def test_removed_participant_loses_access(api, shared_trip):
    r = await api.as_("ana").delete(f"/trips/{shared_trip['id']}/members/carla")
    assert r.status_code == 204
    r = await api.as_("carla").get(f"/trips/{shared_trip['id']}")
    assert r.status_code == 404


# --- Membership rules ----------------------------------------------------------------


async def test_organizer_role_cannot_be_changed_or_removed(api, shared_trip):
    ana = api.as_("ana")
    r = await ana.put(f"/trips/{shared_trip['id']}/members/ana", json={"role": "viewer"})
    assert r.status_code == 409 and r.json()["code"] == "ORGANIZER_ROLE_FIXED"
    r = await ana.delete(f"/trips/{shared_trip['id']}/members/ana")
    assert r.status_code == 409


@pytest.mark.parametrize("role", ["organizer", "admin", ""])
async def test_only_editor_or_viewer_can_be_granted(api, shared_trip, role):
    r = await api.as_("ana").put(
        f"/trips/{shared_trip['id']}/members/dani", json={"role": role})
    assert r.status_code == 422 and r.json()["details"]["fields"][0]["field"] == "role"


async def test_unknown_user_cannot_be_added(api, shared_trip):
    r = await api.as_("ana").put(
        f"/trips/{shared_trip['id']}/members/mallory", json={"role": "viewer"})
    assert r.status_code == 404 and r.json()["code"] == "USER_NOT_FOUND"


async def test_removing_a_nonmember_is_404(api, shared_trip):
    r = await api.as_("ana").delete(f"/trips/{shared_trip['id']}/members/dani")
    assert r.status_code == 404 and r.json()["code"] == "MEMBER_NOT_FOUND"


async def test_users_directory_lists_test_identities(api):
    r = await api.as_("ana").get("/users")
    assert [u["id"] for u in r.json()] == ["ana", "beto", "carla", "dani"]
