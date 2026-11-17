"""Contract and permissions of every MCP tool, through a real MCP client session over HTTP
(US-12, and the US-02 criterion on assistant tools). No model involved."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.mcp.identity import issue_token
from app.mcp.server import EXPECTED_TOOLS, NOT_FOUND
from evals.support import call, payload

HOTEL = "offer-lis-hotel-central-p2"
TRIP_TOOLS = ["get_trip", "get_itinerary", "get_costs", "get_bookings", "get_documents"]
SEARCH = {"category": "accommodation", "destination": "Lisbon",
          "starts_on": "2030-06-10", "ends_on": "2030-06-14"}


@pytest.fixture
async def planned(api, shared_trip, mock_control, make_worker):
    """Shared trip with an item, a confirmed booking and a complete document."""
    ana = api.as_("ana")
    view = (await ana.post(f"/trips/{shared_trip['id']}/itinerary/items",
                           json={"version": 1, "offerId": HOTEL})).json()
    item = view["items"][0]
    await ana.post(f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/bookings",
                   json={"expectedRevision": item["quote"]["revision"],
                         "expectedPrice": item["quote"]["price"]})
    await ana.post(f"/trips/{shared_trip['id']}/documents")
    await make_worker().run_once()
    return shared_trip


async def test_tools_are_discovered_with_schemas_and_read_only_hints(mcp_as):
    async with mcp_as("carla") as tools:
        specs = await tools.list_tools()
    assert {s.name for s in specs} == EXPECTED_TOOLS
    for spec in specs:
        assert spec.description and spec.input_schema["type"] == "object"
    search = next(s for s in specs if s.name == "search_offers")
    assert set(search.input_schema["required"]) == {"trip_id", "category", "destination"}


@pytest.mark.parametrize("user", ["ana", "beto", "carla"])  # organizer, editor, viewer
async def test_every_participant_reads_through_every_tool(mcp_as, planned, user):
    trip_id = planned["id"]
    async with mcp_as(user) as tools:
        trips = payload(await call(tools, "list_my_trips"))["trips"]
        trip = payload(await call(tools, "get_trip", trip_id=trip_id))
        itinerary = payload(await call(tools, "get_itinerary", trip_id=trip_id))
        costs = payload(await call(tools, "get_costs", trip_id=trip_id))
        bookings = payload(await call(tools, "get_bookings", trip_id=trip_id))["bookings"]
        documents = payload(await call(tools, "get_documents", trip_id=trip_id))["documents"]
        search = payload(await call(tools, "search_offers", trip_id=trip_id, **SEARCH))
    assert [t["id"] for t in trips] == [trip_id]
    assert trip["myRole"] == {"ana": "organizer", "beto": "editor", "carla": "viewer"}[user]
    assert itinerary["items"][0]["offerId"] == HOTEL and "rules" in itinerary
    assert {g["code"] for g in itinerary["missing"]} >= {"NO_ACCOMMODATION_FOR_DESTINATION"}
    assert costs["confirmedTotal"]["amount"] == "670.00"
    assert bookings[0]["status"] == "confirmed" and bookings[0]["nextStep"]
    assert documents[0]["status"] == "complete"
    assert search["outcome"] == "complete" and search["offers"]
    assert all(o["priceType"] == "estimate" for o in search["offers"])


async def test_nonparticipant_gets_not_found_on_every_trip_tool(mcp_as, planned):
    async with mcp_as("dani") as tools:
        assert payload(await call(tools, "list_my_trips"))["trips"] == []
        results = [await call(tools, name, trip_id=planned["id"]) for name in TRIP_TOOLS]
        results.append(await call(tools, "search_offers", trip_id=planned["id"], **SEARCH))
        missing = await call(tools, "get_trip", trip_id="00000000-0000-0000-0000-000000000000")
    for result in results + [missing]:
        assert result.is_error and NOT_FOUND in result.content
    # Same answer as for a trip that does not exist: no existence leak.
    assert {r.content.split(": ", 1)[1] for r in results} == {missing.content.split(": ", 1)[1]}


@pytest.mark.parametrize("token", [
    None, "not-a-token",
    issue_token("wrong-secret", "ana", datetime.now(UTC), 300),
    issue_token("dev-only-insecure-mcp-secret", "ana", datetime.now(UTC) - timedelta(hours=1),
                60),
])
async def test_sessions_without_a_valid_identity_get_nothing(mcp_as, planned, token):
    async with mcp_as(token=token or "") as tools:
        for name in ["list_my_trips", *TRIP_TOOLS]:
            args = {} if name == "list_my_trips" else {"trip_id": planned["id"]}
            result = await call(tools, name, **args)
            assert result.is_error and "Not authenticated" in result.content


async def test_identity_cannot_be_passed_as_a_tool_argument(mcp_as, planned):
    async with mcp_as("dani") as tools:
        result = await call(tools, "get_trip", trip_id=planned["id"], user_id="ana")
    assert result.is_error  # unknown argument or not found: never Ana's trip
    assert planned["name"] not in result.content


async def test_partial_and_empty_searches_keep_their_outcome(mcp_as, planned, mock_control):
    await mock_control.post("/scenario", json={"supplierId": "supplier-b",
                                               "mode": "unavailable"})
    async with mcp_as("carla") as tools:
        partial = payload(await call(tools, "search_offers", trip_id=planned["id"], **SEARCH))
        await mock_control.delete("/scenario")
        empty = payload(await call(tools, "search_offers", trip_id=planned["id"],
                                   **{**SEARCH, "starts_on": "2030-06-20",
                                      "ends_on": "2030-06-22"}))
    assert partial["outcome"] == "partial"
    assert [f["providerId"] for f in partial["failedSuppliers"]] == ["supplier-b"]
    assert partial["offers"]
    assert empty["outcome"] == "empty" and empty["offers"] == []


async def test_failed_search_is_a_tool_error_not_an_empty_result(mcp_as, planned, mock_control):
    for supplier in ("supplier-a", "supplier-b"):
        await mock_control.post("/scenario", json={"supplierId": supplier,
                                                   "mode": "unavailable"})
    async with mcp_as("carla") as tools:
        result = await call(tools, "search_offers", trip_id=planned["id"], **SEARCH)
    assert result.is_error
    detail = json.loads(result.content.split("available: ", 1)[1])
    assert detail["outcome"] == "failed" and detail["error"]["code"] == "PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize("args,fragment", [
    ({"category": "cruise", "destination": "Lisbon"}, "category"),
    ({"category": "transport", "destination": "Lisbon"}, "origin"),
    ({"category": "activity", "destination": "Lisbon", "starts_on": "next week"}, "YYYY-MM-DD"),
    ({"category": "activity", "destination": "Lisbon", "starts_on": "2030-05-01"},
     "within the trip dates"),
])
async def test_invalid_arguments_are_explained_as_tool_errors(mcp_as, planned, args, fragment):
    async with mcp_as("carla") as tools:
        result = await call(tools, "search_offers", trip_id=planned["id"], **args)
    assert result.is_error and fragment in result.content


async def test_unknown_tools_such_as_writes_do_not_exist(mcp_as, planned, api):
    async with mcp_as("ana") as tools:
        for name in ("create_booking", "add_item", "update_trip", "request_document"):
            result = await call(tools, name, trip_id=planned["id"])
            assert result.is_error
    bookings = (await api.as_("ana").get(f"/trips/{planned['id']}/bookings")).json()
    assert len(bookings) == 1  # unchanged
