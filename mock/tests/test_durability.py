"""Accepted bookings, references and idempotency records survive lost responses and
provider restarts (contract: info.description and POST /bookings)."""

import asyncio

from provider_mock.app import create_app

HOTEL = "offer-lis-hotel-central-p2"


async def accepted_payload(client):
    offer = (await client.get(f"/v1/offers/{HOTEL}")).json()
    return {
        "clientReference": "trip-1-hotel",
        "offerId": offer["id"],
        "expectedOfferRevision": offer["revision"],
        "expectedPrice": offer["price"],
    }


async def test_bookings_and_idempotency_survive_restart(make_client, contract, settings, clock):
    async with make_client() as before:
        payload = await accepted_payload(before)
        r = await before.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "k1"})
        booking = contract.check(r, "POST", "/bookings")

    restarted = create_app(settings, clock)  # new process state, same data file
    async with make_client(target=restarted) as after:
        r = await after.get(f"/v1/bookings/{booking['id']}")
        assert contract.check(r, "GET", "/bookings/{bookingId}") == booking

        r = await after.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "k1"})
        assert r.status_code == 200 and r.json() == booking

        r = await after.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "k2"})
        assert r.status_code == 409 and r.json()["code"] == "REFERENCE_CONFLICT"

        # Quote revisions are durable too: the same quote is still current.
        offer = (await after.get(f"/v1/offers/{HOTEL}")).json()
        assert offer["revision"] == payload["expectedOfferRevision"]

        # New bookings keep unique ids after restart.
        other = (await after.get("/v1/offers/offer-lis-tram-tour-p2")).json()
        r = await after.post("/v1/bookings", headers={"Idempotency-Key": "k3"}, json={
            "clientReference": "trip-1-tram", "offerId": other["id"],
            "expectedOfferRevision": other["revision"], "expectedPrice": other["price"],
        })
        assert r.status_code == 201 and r.json()["id"] != booking["id"]


async def test_lost_response_leaves_a_reconcilable_booking(client, contract):
    """Supplier 'timeout': the booking is committed but the response arrives late.
    A client that gives up can reconcile by reference and by idempotent replay."""
    payload = await accepted_payload(client)
    await client.post(
        "/_control/scenario",
        json={"supplierId": "supplier-a", "mode": "timeout", "delaySeconds": 5},
    )
    request = client.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "k1"})
    try:
        await asyncio.wait_for(request, timeout=0.2)
        raise AssertionError("expected the client to give up waiting")
    except TimeoutError:
        pass

    await client.delete("/_control/scenario")
    r = await client.get("/v1/bookings/by-reference/trip-1-hotel")
    found = contract.check(r, "GET", "/bookings/by-reference/{clientReference}")
    assert found["status"] == "confirmed"

    r = await client.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "k1"})
    assert r.status_code == 200 and r.json()["id"] == found["id"]


async def test_reset_clears_data_and_controls(client):
    payload = await accepted_payload(client)
    await client.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "k1"})
    await client.post(
        "/_control/scenario", json={"supplierId": "supplier-b", "mode": "unavailable"}
    )

    r = await client.post("/_control/reset")
    assert r.status_code == 200
    assert (await client.get("/v1/bookings/by-reference/trip-1-hotel")).status_code == 404
    assert (await client.get("/_control/scenario")).json() == {"scenarios": {}}
    assert (await client.get("/_control/clock")).json()["frozen"] is False
