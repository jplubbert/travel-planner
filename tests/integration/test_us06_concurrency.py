"""US-06 — Edit without silently losing changes (ADR 0001), against real PostgreSQL."""

import asyncio

import pytest

HOTEL = "offer-lis-hotel-central-p2"
TRAM = "offer-lis-tram-tour-p2"
FADO = "offer-lis-fado-night-p2"
SINTRA = "offer-lis-sintra-day-p2"
FOOD = "offer-lis-food-walk-p2"


def add(client, trip, offer_id, version):
    return client.post(f"/trips/{trip['id']}/itinerary/items",
                       json={"version": version, "offerId": offer_id})


async def test_two_editors_at_once_one_wins_the_other_learns_why_and_nothing_is_lost(
    api, shared_trip, mock_control
):
    ana, beto = api.as_("ana"), api.as_("beto")
    r_ana, r_beto = await asyncio.gather(add(ana, shared_trip, HOTEL, 1),
                                         add(beto, shared_trip, TRAM, 1))
    assert sorted([r_ana.status_code, r_beto.status_code]) == [201, 409]
    winner, loser = (r_ana, r_beto) if r_ana.status_code == 201 else (r_beto, r_ana)
    loser_client, loser_offer = (beto, TRAM) if loser is r_beto else (ana, HOTEL)
    winner_offer = HOTEL if loser_offer == TRAM else TRAM

    # The loser can tell their change was not saved, and why.
    body = loser.json()
    assert body["code"] == "TRIP_VERSION_CONFLICT" and "Nothing was saved" in body["message"]
    details = body["details"]
    assert details["yourVersion"] == 1 and details["currentVersion"] == 2
    [change] = details["changesSinceYourVersion"]
    assert change["version"] == 2 and change["kind"] == "item_added"
    assert change["by"] == ("ana" if winner is r_ana else "beto")
    assert [i["offerId"] for i in details["currentItinerary"]["items"]] == [winner_offer]
    assert details["current"]["version"] == 2

    # Resolving: reapply on the current version. Both changes are kept.
    r = await add(loser_client, shared_trip, loser_offer, details["currentVersion"])
    assert r.status_code == 201
    assert {i["offerId"] for i in r.json()["items"]} == {HOTEL, TRAM}
    assert r.json()["tripVersion"] == 3


async def test_many_simultaneous_edits_exactly_one_per_version(api, shared_trip, mock_control):
    clients = [api.as_("ana"), api.as_("beto")]
    offers = [HOTEL, TRAM, FADO, SINTRA, FOOD]
    responses = await asyncio.gather(*(add(clients[n % 2], shared_trip, offer, 1)
                                       for n, offer in enumerate(offers)))
    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409, 409]
    saved = (await clients[0].get(f"/trips/{shared_trip['id']}/itinerary")).json()
    assert len(saved["items"]) == 1 and saved["tripVersion"] == 2


async def test_trip_edit_and_itinerary_edit_share_one_version(api, shared_trip, mock_control):
    ana, beto = api.as_("ana"), api.as_("beto")
    r_patch, r_add = await asyncio.gather(
        ana.patch(f"/trips/{shared_trip['id']}", json={"version": 1, "budget": "5000"}),
        add(beto, shared_trip, HOTEL, 1),
    )
    assert sorted([r_patch.status_code, r_add.status_code]) == [200, 409]
    loser = r_patch if r_patch.status_code == 409 else r_add
    assert loser.json()["details"]["changesSinceYourVersion"][0]["kind"] in (
        "trip_updated", "item_added")


async def test_conflict_lists_every_change_since_the_callers_version(
    api, shared_trip, mock_control
):
    beto = api.as_("beto")
    await add(beto, shared_trip, HOTEL, 1)
    await add(beto, shared_trip, TRAM, 2)
    await beto.patch(f"/trips/{shared_trip['id']}", json={"version": 3, "name": "Renamed"})
    r = await add(api.as_("ana"), shared_trip, FADO, 1)  # Ana still has version 1
    assert r.status_code == 409
    changes = r.json()["details"]["changesSinceYourVersion"]
    assert [(c["version"], c["kind"], c["by"]) for c in changes] == [
        (2, "item_added", "beto"), (3, "item_added", "beto"), (4, "trip_updated", "beto")]
    assert "name" in changes[2]["summary"]


@pytest.mark.parametrize("mutation", ["add", "remove", "reorder", "refresh", "patch"])
async def test_viewer_gets_403_before_409(api, shared_trip, mock_control, mutation):
    view = (await add(api.as_("ana"), shared_trip, HOTEL, 1)).json()
    item_id = view["items"][0]["itemId"]
    carla, stale = api.as_("carla"), 1  # stale version: an editor would get 409
    base = f"/trips/{shared_trip['id']}"
    r = await {
        "add": lambda: add(carla, shared_trip, TRAM, stale),
        "remove": lambda: carla.delete(f"{base}/itinerary/items/{item_id}",
                                       params={"version": stale}),
        "reorder": lambda: carla.put(f"{base}/itinerary/order",
                                     json={"version": stale, "itemIds": [item_id]}),
        "refresh": lambda: carla.post(f"{base}/itinerary/items/{item_id}/refresh",
                                      json={"version": stale}),
        "patch": lambda: carla.patch(base, json={"version": stale, "name": "x"}),
    }[mutation]()
    assert r.status_code == 403
    assert "currentItinerary" not in r.text  # a refused viewer learns nothing extra


@pytest.mark.parametrize("mutation", ["add", "remove", "reorder", "refresh"])
async def test_every_itinerary_mutation_requires_the_version(api, shared_trip, mock_control,
                                                             mutation):
    view = (await add(api.as_("ana"), shared_trip, HOTEL, 1)).json()
    item_id = view["items"][0]["itemId"]
    ana, base = api.as_("ana"), f"/trips/{shared_trip['id']}"
    r = await {
        "add": lambda: ana.post(f"{base}/itinerary/items", json={"offerId": TRAM}),
        "remove": lambda: ana.delete(f"{base}/itinerary/items/{item_id}"),
        "reorder": lambda: ana.put(f"{base}/itinerary/order", json={"itemIds": [item_id]}),
        "refresh": lambda: ana.post(f"{base}/itinerary/items/{item_id}/refresh", json={}),
    }[mutation]()
    assert r.status_code == 400 and r.json()["code"] == "VERSION_REQUIRED"
    after = (await ana.get(f"{base}/itinerary")).json()
    assert after["tripVersion"] == 2 and len(after["items"]) == 1


@pytest.mark.parametrize("mutation", ["remove", "reorder", "refresh"])
async def test_stale_version_is_refused_for_every_mutation(api, shared_trip, mock_control,
                                                           mutation):
    view = (await add(api.as_("ana"), shared_trip, HOTEL, 1)).json()
    item_id = view["items"][0]["itemId"]
    beto, base = api.as_("beto"), f"/trips/{shared_trip['id']}"
    await mock_control.post(f"/offers/{HOTEL}", json={"price": "700.00"})
    r = await {
        "remove": lambda: beto.delete(f"{base}/itinerary/items/{item_id}",
                                      params={"version": 1}),
        "reorder": lambda: beto.put(f"{base}/itinerary/order",
                                    json={"version": 1, "itemIds": [item_id]}),
        "refresh": lambda: beto.post(f"{base}/itinerary/items/{item_id}/refresh",
                                     json={"version": 1}),
    }[mutation]()
    assert r.status_code == 409 and r.json()["details"]["currentVersion"] == 2
    after = (await beto.get(f"{base}/itinerary")).json()
    assert after["items"] == view["items"] and after["tripVersion"] == 2


@pytest.mark.parametrize("round_", range(8))
async def test_concurrent_remove_and_reorder(api, shared_trip, mock_control, round_):
    """Regression: the loser must get 409, never a validation error computed on the
    winner's newer state (seen once as 422 before the trip row was locked)."""
    ana, beto = api.as_("ana"), api.as_("beto")
    await add(ana, shared_trip, HOTEL, 1)
    view = (await add(ana, shared_trip, TRAM, 2)).json()
    ids = [i["itemId"] for i in view["items"]]
    base = f"/trips/{shared_trip['id']}"
    r_remove, r_order = await asyncio.gather(
        ana.delete(f"{base}/itinerary/items/{ids[0]}", params={"version": 3}),
        beto.put(f"{base}/itinerary/order", json={"version": 3, "itemIds": ids[::-1]}),
    )
    assert sorted([r_remove.status_code, r_order.status_code]) == [200, 409]
    loser = r_remove if r_remove.status_code == 409 else r_order
    assert loser.json()["code"] == "TRIP_VERSION_CONFLICT"
    saved = (await ana.get(f"{base}/itinerary")).json()
    assert saved["tripVersion"] == 4
    if r_remove.status_code == 200:
        assert [i["itemId"] for i in saved["items"]] == [ids[1]]
    else:
        assert [i["itemId"] for i in saved["items"]] == ids[::-1]


@pytest.mark.parametrize("round_", range(5))
async def test_two_editors_adding_the_same_offer(api, shared_trip, mock_control, round_):
    r1, r2 = await asyncio.gather(add(api.as_("ana"), shared_trip, HOTEL, 1),
                                  add(api.as_("beto"), shared_trip, HOTEL, 1))
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["code"] == "TRIP_VERSION_CONFLICT"
    saved = (await api.as_("ana").get(f"/trips/{shared_trip['id']}/itinerary")).json()
    assert [i["offerId"] for i in saved["items"]] == [HOTEL]


async def test_validation_reads_the_same_version_it_checked(
    api, shared_trip, mock_control, database_url
):
    """Deterministic version of the race above. Another transaction commits a removal
    while our reorder is between its version check and its read of the items. The reorder
    must answer 409 (a version conflict), not 422 computed on the newer itinerary."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    ana = api.as_("ana")
    await add(ana, shared_trip, HOTEL, 1)
    view = (await add(ana, shared_trip, TRAM, 2)).json()
    ids = [i["itemId"] for i in view["items"]]

    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as other:
        transaction = await other.begin()
        # A plain SELECT on itinerary_items waits behind this lock, which freezes the
        # reorder exactly after its version check if that check did not lock the trip.
        await other.execute(text("LOCK TABLE itinerary_items IN ACCESS EXCLUSIVE MODE"))
        await other.execute(text("UPDATE trips SET version = 4 WHERE id = :id"),
                            {"id": shared_trip["id"]})
        await other.execute(text("DELETE FROM itinerary_items WHERE id = :id"), {"id": ids[0]})
        await other.execute(text(
            "INSERT INTO trip_changes VALUES (:id, 4, 'ana', 'item_removed', 'Removed', now())"),
            {"id": shared_trip["id"]})

        reorder = asyncio.create_task(api.as_("beto").put(
            f"/trips/{shared_trip['id']}/itinerary/order",
            json={"version": 3, "itemIds": ids[::-1]}))
        await asyncio.sleep(0.5)  # let the request reach the point where it blocks
        assert not reorder.done()
        await transaction.commit()
        r = await asyncio.wait_for(reorder, timeout=5)
    await engine.dispose()

    assert r.status_code == 409, r.text
    assert r.json()["details"]["changesSinceYourVersion"][0]["kind"] == "item_removed"
