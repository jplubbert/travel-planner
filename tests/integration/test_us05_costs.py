"""US-05 — Understand the budget (PostgreSQL + provider mock over HTTP)."""

from decimal import Decimal

HOTEL = "offer-lis-hotel-central-p2"  # 670.00
BUS = "offer-mad-bus-lis-p2"  # 2 x 39.99 = 79.98
CRUISE = "offer-par-seine-cruise-p2"  # 2 x 22.50 = 45.00
APARTMENT = "offer-par-apartment-montmartre-p2"  # 720 + 2 x 60 = 840.00; stays pending
SURF = "offer-lis-surf-lesson-p2"  # 2 x 59.99 = 119.98; accepted, then failed


async def add(client, trip, offer_id):
    version = (await client.get(f"/trips/{trip['id']}")).json()["version"]
    r = await client.post(f"/trips/{trip['id']}/itinerary/items",
                          json={"version": version, "offerId": offer_id})
    assert r.status_code == 201, r.text
    return r.json()


async def costs(client, trip):
    r = await client.get(f"/trips/{trip['id']}/costs")
    assert r.status_code == 200, r.text
    return r.json()


async def test_itemized_costs_total_and_budget_in_usd(api, shared_trip, mock_control):
    for offer in (HOTEL, BUS, CRUISE):
        view = await add(api.as_("ana"), shared_trip, offer)
    data = await costs(api.as_("carla"), shared_trip)  # viewers can read costs
    assert data["currency"] == "USD" and data["tripVersion"] == view["tripVersion"]
    assert [(line["title"], line["amount"]["amount"], line["basis"]) for line in data["lines"]] == [
        (i["title"], i["quote"]["price"]["amount"], "estimate") for i in view["items"]]
    assert data["estimatedTotal"]["amount"] == "794.98"  # 670.00 + 79.98 + 45.00
    assert data["confirmedTotal"]["amount"] == "0.00"
    assert data["total"] == {"amount": "794.98", "currency": "USD"}
    budget = data["budget"]
    assert budget["amount"]["amount"] == "4500.00"
    assert budget["remaining"]["amount"] == "3705.02"
    assert budget["overBudget"] is False
    assert Decimal(budget["usedPercent"]) == Decimal("17.7")  # 17.666 -> 17.7
    assert "half-up" in data["rounding"]


async def test_costs_follow_itinerary_changes(api, shared_trip, mock_control):
    view = await add(api.as_("ana"), shared_trip, HOTEL)
    view = await add(api.as_("ana"), shared_trip, BUS)
    bus = next(i for i in view["items"] if i["offerId"] == BUS)
    await api.as_("ana").delete(f"/trips/{shared_trip['id']}/itinerary/items/{bus['itemId']}",
                                params={"version": view["tripVersion"]})
    data = await costs(api.as_("ana"), shared_trip)
    assert [line["title"] for line in data["lines"]] == [view["items"][0]["title"]]
    assert data["total"]["amount"] == "670.00"


async def test_over_budget_is_reported(api, trip_payload, mock_control):
    tight = (await api.as_("ana").post("/trips", json={**trip_payload, "budget": "700"})).json()
    await add(api.as_("ana"), tight, HOTEL)
    await add(api.as_("ana"), tight, BUS)
    budget = (await costs(api.as_("ana"), tight))["budget"]
    assert budget["overBudget"] is True and budget["remaining"]["amount"] == "-49.98"
    assert budget["usedPercent"] == "107.1"


def book(client, trip, item):
    return client.post(f"/trips/{trip['id']}/itinerary/items/{item['itemId']}/bookings",
                       json={"expectedRevision": item["quote"]["revision"],
                             "expectedPrice": item["quote"]["price"]})


async def test_confirmed_price_survives_provider_price_changes(api, shared_trip, mock_control):
    """With a real booking through the provider (US-07), not an inserted row."""
    ana = api.as_("ana")
    view = await add(ana, shared_trip, HOTEL)
    item = view["items"][0]
    booking = (await book(ana, shared_trip, item)).json()
    assert booking["status"] == "confirmed"

    # The provider's price changes and the item's quote is refreshed...
    await mock_control.post(f"/offers/{HOTEL}", json={"price": "820.00"})
    r = await ana.post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/refresh",
        json={"version": view["tripVersion"]})
    assert r.json()["items"][0]["quote"]["price"]["amount"] == "820.00"

    # ...but the cost stays the accepted, confirmed price.
    data = await costs(ana, shared_trip)
    line = data["lines"][0]
    assert line["basis"] == "confirmed" and line["amount"]["amount"] == "670.00"
    assert line["bookingId"] == booking["bookingId"]
    assert line["bookingStatus"] == "confirmed" and "accepted" in line["note"]
    assert data["confirmedTotal"]["amount"] == "670.00"
    assert data["estimatedTotal"]["amount"] == "0.00"


async def test_pending_booking_is_not_presented_as_confirmed(api, shared_trip, mock_control,
                                                             make_worker):
    ana = api.as_("ana")
    view = await add(ana, shared_trip, APARTMENT)
    booking = (await book(ana, shared_trip, view["items"][0])).json()
    assert booking["status"] == "pending"
    data = await costs(ana, shared_trip)
    line = data["lines"][0]
    assert line["basis"] == "estimate" and "pending" in line["note"]
    assert data["confirmedTotal"]["amount"] == "0.00"

    # Once the provider confirms and the worker reconciles, the cost becomes confirmed.
    await mock_control.post(f"/booking/{booking['providerBookingId']}/settle",
                            json={"status": "confirmed"})
    await make_worker().run_once()
    line = (await costs(ana, shared_trip))["lines"][0]
    assert line["basis"] == "confirmed" and line["amount"]["amount"] == "840.00"


async def test_failed_and_rejected_bookings_leave_estimates(api, make_app, shared_trip,
                                                            mock_control, provider_client):
    ana = api.as_("ana")
    await add(ana, shared_trip, SURF)
    view = await add(ana, shared_trip, HOTEL)
    surf, hotel = (next(i for i in view["items"] if i["offerId"] == o) for o in (SURF, HOTEL))
    assert (await book(ana, shared_trip, surf)).json()["status"] == "failed"

    async def price_changes_now():
        await mock_control.post(f"/offers/{HOTEL}", json={"price": "999.00"})

    class Racy:
        def __getattr__(self, name):
            return getattr(provider_client, name)

        async def create_booking(self, request, *, idempotency_key):
            await price_changes_now()
            return await provider_client.create_booking(request,
                                                        idempotency_key=idempotency_key)

    racy = api.as_("ana", app=make_app(provider=Racy()))
    assert (await book(racy, shared_trip, hotel)).json()["status"] == "rejected"
    data = await costs(ana, shared_trip)
    assert {line["basis"] for line in data["lines"]} == {"estimate"}
    assert data["confirmedTotal"]["amount"] == "0.00"
    assert data["total"]["amount"] == "789.98"  # 119.98 surf + 670.00 hotel estimates
