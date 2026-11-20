"""US-04 — Build an itinerary (PostgreSQL + provider mock over HTTP)."""

import pytest

HOTEL = "offer-lis-hotel-central-p2"  # 670.00, 2030-06-10 15:00 -> 06-14 11:00 (+01:00)
TRAM = "offer-lis-tram-tour-p2"  # 2030-06-11 10:00-12:00
SINTRA = "offer-lis-sintra-day-p2"  # 2030-06-11 09:00-17:30 (overlaps the tram tour)
BELEM = "offer-lis-hotel-belem-p2"  # unavailable


async def itinerary(client, trip):
    r = await client.get(f"/trips/{trip['id']}/itinerary")
    assert r.status_code == 200, r.text
    return r.json()


async def add(client, trip, offer_id, version):
    return await client.post(f"/trips/{trip['id']}/itinerary/items",
                             json={"version": version, "offerId": offer_id})


async def add_ok(client, trip, offer_id):
    version = (await itinerary(client, trip))["tripVersion"]
    r = await add(client, trip, offer_id, version)
    assert r.status_code == 201, r.text
    return r.json()


def by_offer(view, offer_id):
    return next(i for i in view["items"] if i["offerId"] == offer_id)


def flag_codes(item):
    return {f["code"] for f in item["flags"]}


# --- Adding, with the quote taken from the provider ---------------------------------------


async def test_editor_adds_an_option_with_schedule_and_cost(api, shared_trip, mock_control):
    r = await add(api.as_("beto"), shared_trip, HOTEL, 1)
    assert r.status_code == 201
    view = r.json()
    assert view["tripVersion"] == 2
    item = by_offer(view, HOTEL)
    assert item["position"] == 0 and item["addedBy"] == "beto"
    assert item["startsAt"] == "2030-06-10T15:00:00+01:00"
    assert item["endsAt"] == "2030-06-14T11:00:00+01:00"
    assert item["quote"]["price"] == {"amount": "670.00", "currency": "USD"}
    assert item["quote"]["priceType"] == "estimate" and item["quote"]["expired"] is False
    assert item["restrictions"] == ["Non-refundable simulated booking"]
    assert item["booking"] is None


async def test_price_always_comes_from_the_provider(api, shared_trip, mock_control):
    # The client cannot send a price at all...
    r = await api.as_("ana").post(f"/trips/{shared_trip['id']}/itinerary/items", json={
        "version": 1, "offerId": HOTEL, "price": {"amount": "1.00", "currency": "USD"}})
    assert r.status_code == 422
    # ...and what is stored is the provider's current quote.
    await mock_control.post(f"/offers/{HOTEL}", json={"price": "699.00"})
    view = await add_ok(api.as_("ana"), shared_trip, HOTEL)
    assert by_offer(view, HOTEL)["quote"]["price"]["amount"] == "699.00"


@pytest.mark.parametrize("offer_id,status,code_or_field", [
    (BELEM, 409, "OFFER_UNAVAILABLE"),
    ("offer-lis-hotel-central-p3", 422, "offerId"),  # quoted for 3 travelers, trip has 2
    ("offer-does-not-exist-p2", 422, "offerId"),
    ("", 422, "offerId"),
])
async def test_unusable_offers_are_rejected_and_nothing_changes(
    api, shared_trip, mock_control, offer_id, status, code_or_field
):
    r = await add(api.as_("ana"), shared_trip, offer_id, 1)
    assert r.status_code == status
    body = r.json()
    if status == 409:
        assert body["code"] == code_or_field
    else:
        assert code_or_field in [f["field"] for f in body["details"]["fields"]]
    view = await itinerary(api.as_("ana"), shared_trip)
    assert view["items"] == [] and view["tripVersion"] == 1


async def test_the_same_offer_cannot_be_added_twice(api, shared_trip, mock_control):
    await add_ok(api.as_("ana"), shared_trip, HOTEL)
    r = await add(api.as_("ana"), shared_trip, HOTEL, 2)
    assert r.status_code == 409 and r.json()["code"] == "ITEM_ALREADY_IN_ITINERARY"


async def test_provider_failure_while_adding_saves_nothing(api, shared_trip, mock_control):
    await mock_control.post("/scenario", json={"supplierId": "supplier-a", "mode": "unavailable"})
    r = await add(api.as_("ana"), shared_trip, HOTEL, 1)
    assert r.status_code == 503 and r.json()["code"] == "PROVIDER_UNAVAILABLE"
    view = await itinerary(api.as_("ana"), shared_trip)
    assert view["items"] == [] and view["tripVersion"] == 1


# --- Removing and reordering --------------------------------------------------------------


async def test_remove_and_reorder(api, shared_trip, mock_control):
    beto = api.as_("beto")
    for offer in (HOTEL, TRAM, SINTRA):
        view = await add_ok(beto, shared_trip, offer)
    ids = [i["itemId"] for i in view["items"]]

    r = await beto.put(f"/trips/{shared_trip['id']}/itinerary/order",
                       json={"version": view["tripVersion"], "itemIds": ids[::-1]})
    assert r.status_code == 200
    assert [i["itemId"] for i in r.json()["items"]] == ids[::-1]
    assert [i["position"] for i in r.json()["items"]] == [0, 1, 2]

    r = await beto.delete(f"/trips/{shared_trip['id']}/itinerary/items/{ids[1]}",
                          params={"version": r.json()["tripVersion"]})
    assert r.status_code == 200
    remaining = r.json()["items"]
    assert [i["itemId"] for i in remaining] == [ids[2], ids[0]]
    assert [i["position"] for i in remaining] == [0, 1]  # positions stay compact


@pytest.mark.parametrize("item_ids", ["missing-one", "duplicate", "unknown", "not-a-list"])
async def test_reorder_must_be_a_permutation(api, shared_trip, mock_control, item_ids):
    await add_ok(api.as_("ana"), shared_trip, HOTEL)
    view = await add_ok(api.as_("ana"), shared_trip, TRAM)
    ids = [i["itemId"] for i in view["items"]]
    payload = {"missing-one": ids[:1], "duplicate": [ids[0], ids[0]],
               "unknown": [ids[0], "00000000-0000-0000-0000-000000000000"],
               "not-a-list": None}[item_ids]
    r = await api.as_("ana").put(f"/trips/{shared_trip['id']}/itinerary/order",
                                 json={"version": 3, "itemIds": payload})
    assert r.status_code == 422
    assert (await itinerary(api.as_("ana"), shared_trip))["tripVersion"] == 3


async def test_removing_unknown_item_is_404(api, shared_trip, mock_control):
    for item_id in ("00000000-0000-0000-0000-000000000000", "nope"):
        r = await api.as_("ana").delete(
            f"/trips/{shared_trip['id']}/itinerary/items/{item_id}", params={"version": 1})
        assert r.status_code == 404 and r.json()["code"] == "ITEM_NOT_FOUND"


async def test_item_of_another_trip_cannot_be_touched(api, shared_trip, trip_payload,
                                                      mock_control):
    other = (await api.as_("ana").post("/trips", json=trip_payload)).json()
    view = await add_ok(api.as_("ana"), other, HOTEL)
    foreign = view["items"][0]["itemId"]
    r = await api.as_("ana").delete(f"/trips/{shared_trip['id']}/itinerary/items/{foreign}",
                                    params={"version": 1})
    assert r.status_code == 404
    assert len((await itinerary(api.as_("ana"), other))["items"]) == 1


@pytest.mark.parametrize("status", ["submitted", "unknown", "pending", "confirmed"])
async def test_item_with_an_active_booking_cannot_be_removed(
    api, shared_trip, mock_control, add_booking, status
):
    view = await add_ok(api.as_("ana"), shared_trip, HOTEL)
    item = view["items"][0]
    await add_booking(shared_trip["id"], item, status,
                      "670.00" if status in ("pending", "confirmed") else None)
    r = await api.as_("ana").delete(
        f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}", params={"version": 2})
    assert r.status_code == 409 and r.json()["code"] == "ITEM_HAS_BOOKING"


# --- Read-time flags and missing information ---------------------------------------------


async def test_schedule_conflicts_are_flagged_on_both_items(api, shared_trip, mock_control):
    await add_ok(api.as_("ana"), shared_trip, HOTEL)
    await add_ok(api.as_("ana"), shared_trip, TRAM)
    view = await add_ok(api.as_("ana"), shared_trip, SINTRA)
    tram, sintra, hotel = (by_offer(view, o) for o in (TRAM, SINTRA, HOTEL))
    conflict = next(f for f in tram["flags"] if f["code"] == "SCHEDULE_CONFLICT")
    assert conflict["relatedItemIds"] == [sintra["itemId"]]
    assert "SCHEDULE_CONFLICT" in flag_codes(sintra)
    assert "SCHEDULE_CONFLICT" not in flag_codes(hotel)  # activities during a stay are fine


async def test_expired_prices_are_flagged(api, shared_trip, mock_control):
    await mock_control.post("/clock", json={"now": "2026-01-01T00:00:00Z"})  # old quote
    view = await add_ok(api.as_("ana"), shared_trip, HOTEL)
    item = view["items"][0]
    assert item["quote"]["expired"] is True and "PRICE_EXPIRED" in flag_codes(item)


async def test_unavailability_found_on_refresh_is_flagged(api, shared_trip, mock_control):
    await add_ok(api.as_("ana"), shared_trip, HOTEL)
    await mock_control.post(f"/offers/{HOTEL}", json={"available": False})
    view = (await itinerary(api.as_("ana"), shared_trip))
    item_id = view["items"][0]["itemId"]
    r = await api.as_("ana").post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item_id}/refresh", json={"version": 2})
    assert r.status_code == 200 and r.json()["changed"] is True
    assert "OFFER_UNAVAILABLE" in flag_codes(r.json()["items"][0])


async def test_items_outside_the_trip_dates_are_flagged(api, trip_payload, mock_control):
    short = (await api.as_("ana").post("/trips", json={
        **trip_payload, "startsOn": "2030-06-10", "endsOn": "2030-06-12"})).json()
    # Search is limited to trip dates, but an existing item can fall outside after the
    # trip dates change.
    await add_ok(api.as_("ana"), short, TRAM)
    r = await api.as_("ana").patch(f"/trips/{short['id']}",
                                   json={"version": 2, "startsOn": "2030-06-12"})
    assert r.status_code == 200
    view = await itinerary(api.as_("ana"), short)
    assert "OUTSIDE_TRIP_DATES" in flag_codes(view["items"][0])


async def test_party_size_change_is_flagged(api, shared_trip, mock_control):
    await add_ok(api.as_("ana"), shared_trip, HOTEL)
    await api.as_("ana").patch(f"/trips/{shared_trip['id']}", json={"version": 2, "travelers": 3})
    view = await itinerary(api.as_("ana"), shared_trip)
    assert "PARTY_SIZE_MISMATCH" in flag_codes(view["items"][0])


async def test_missing_information_and_rules_are_explained(api, shared_trip, mock_control):
    empty = await itinerary(api.as_("carla"), shared_trip)
    assert [g["code"] for g in empty["missing"]] == ["EMPTY_ITINERARY"]
    assert any("Accommodation only conflicts" in rule for rule in empty["rules"])

    view = await add_ok(api.as_("ana"), shared_trip, HOTEL)
    missing = {(g["code"], g["message"]) for g in view["missing"]}
    assert ("NO_ACCOMMODATION_FOR_DESTINATION", "No accommodation in Madrid.") in missing
    assert ("NO_ACCOMMODATION_FOR_DESTINATION", "No accommodation in Paris.") in missing
    assert ("NIGHTS_WITHOUT_ACCOMMODATION",
            "No accommodation for the nights of 2030-06-14 to 2030-06-21.") in missing


async def test_missing_transport_between_consecutive_destinations(
    api, shared_trip, mock_control
):
    def transport_gaps(view):
        return [g["message"] for g in view["missing"] if g["code"] == "MISSING_TRANSPORT"]

    view = await add_ok(api.as_("ana"), shared_trip, HOTEL)  # Lisbon, Madrid, Paris
    assert transport_gaps(view) == ["Missing transport from Lisbon to Madrid.",
                                    "Missing transport from Madrid to Paris."]
    assert any("consecutive destinations" in rule for rule in view["rules"])

    view = await add_ok(api.as_("ana"), shared_trip, "offer-lis-flight-mad-p2")  # Madrid->Lisbon
    assert len(transport_gaps(view)) == 2  # the wrong direction does not count
    view = await add_ok(api.as_("ana"), shared_trip, "offer-mad-train-lis-p2")  # Lisbon->Madrid
    assert transport_gaps(view) == ["Missing transport from Madrid to Paris."]
    reopened = await itinerary(api.as_("carla"), shared_trip)
    assert transport_gaps(reopened) == ["Missing transport from Madrid to Paris."]


# --- Refreshing a quote ----------------------------------------------------------------


async def test_refresh_updates_the_quote_and_records_what_changed(api, shared_trip, mock_control):
    view = await add_ok(api.as_("beto"), shared_trip, HOTEL)
    item = view["items"][0]
    url = f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/refresh"

    r = await api.as_("beto").post(url, json={"version": 2})
    assert r.status_code == 200 and r.json()["changed"] is False
    assert r.json()["tripVersion"] == 2  # an unchanged quote saves nothing

    await mock_control.post(f"/offers/{HOTEL}", json={"price": "710.00"})
    r = await api.as_("beto").post(url, json={"version": 2})
    body = r.json()
    assert body["changed"] is True and body["tripVersion"] == 3
    assert "670.00 -> 710.00" in body["summary"]
    refreshed = body["items"][0]
    assert refreshed["quote"]["price"]["amount"] == "710.00"
    assert refreshed["quote"]["revision"] != item["quote"]["revision"]
    assert refreshed["quote"]["quotedAt"] >= item["quote"]["quotedAt"]


async def test_refresh_when_provider_fails_keeps_the_old_quote(api, shared_trip, mock_control):
    view = await add_ok(api.as_("ana"), shared_trip, HOTEL)
    item_id = view["items"][0]["itemId"]
    await mock_control.post("/scenario", json={"supplierId": "supplier-a", "mode": "rate_limited"})
    r = await api.as_("ana").post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item_id}/refresh", json={"version": 2})
    assert r.status_code == 503 and r.json()["code"] == "PROVIDER_RATE_LIMITED"
    after = await itinerary(api.as_("ana"), shared_trip)
    assert after["tripVersion"] == 2 and after["items"][0]["quote"] == view["items"][0]["quote"]


# --- Persistence and permissions ----------------------------------------------------------


async def test_saved_itinerary_is_there_when_the_trip_is_reopened(
    api, make_app, shared_trip, mock_control
):
    await add_ok(api.as_("ana"), shared_trip, HOTEL)
    view = await add_ok(api.as_("ana"), shared_trip, TRAM)
    restarted = make_app()
    reopened = await itinerary(api.as_("carla", app=restarted), shared_trip)
    assert reopened["items"] == view["items"] and reopened["tripVersion"] == 3


MUTATIONS = [
    ("POST", "/itinerary/items", {"version": 1, "offerId": HOTEL}, None),
    ("PUT", "/itinerary/order", {"version": 1, "itemIds": []}, None),
    ("DELETE", "/itinerary/items/00000000-0000-0000-0000-000000000000", None, {"version": 1}),
    ("POST", "/itinerary/items/00000000-0000-0000-0000-000000000000/refresh",
     {"version": 1}, None),
]


@pytest.mark.parametrize("method,path,body,params", MUTATIONS)
async def test_viewers_cannot_change_the_itinerary(api, shared_trip, method, path, body, params):
    r = await api.as_("carla").request(method, f"/trips/{shared_trip['id']}{path}",
                                       json=body, params=params)
    assert r.status_code == 403


@pytest.mark.parametrize("method,path,body,params", MUTATIONS + [
    ("GET", "/itinerary", None, None), ("GET", "/costs", None, None)])
async def test_nonparticipants_get_404(api, shared_trip, method, path, body, params):
    r = await api.as_("dani").request(method, f"/trips/{shared_trip['id']}{path}",
                                      json=body, params=params)
    assert r.status_code == 404 and r.json()["code"] == "TRIP_NOT_FOUND"
