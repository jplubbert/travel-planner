import asyncio

import pytest

CREATE = "/bookings"
BY_ID = "/bookings/{bookingId}"
BY_REF = "/bookings/by-reference/{clientReference}"
HOTEL = "offer-lis-hotel-central-p2"


async def quote(client, offer_id=HOTEL):
    r = await client.get(f"/v1/offers/{offer_id}")
    assert r.status_code == 200
    return r.json()


def request_for(offer, reference="trip-1-hotel"):
    return {
        "clientReference": reference,
        "offerId": offer["id"],
        "expectedOfferRevision": offer["revision"],
        "expectedPrice": offer["price"],
    }


async def book(client, contract, payload, key="key-1", *, valid=True):
    if valid:
        contract.check_request("CreateBookingRequest", payload)
    r = await client.post("/v1/bookings", json=payload, headers={"Idempotency-Key": key})
    return r, contract.check(r, "POST", CREATE)


async def test_first_acceptance_returns_201_with_offer_snapshot(client, contract):
    offer = await quote(client)
    r, booking = await book(client, contract, request_for(offer))
    assert r.status_code == 201
    assert booking["status"] == "confirmed"
    assert booking["clientReference"] == "trip-1-hotel"
    assert booking["offer"] == offer
    assert "failure" not in booking
    assert booking["createdAt"] == booking["updatedAt"] == "2030-06-01T12:00:00Z"


async def test_identical_retry_returns_200_with_same_booking(client, contract):
    payload = request_for(await quote(client))
    _, first = await book(client, contract, payload)
    r, replay = await book(client, contract, payload)
    assert r.status_code == 200 and replay == first


async def test_replay_ignores_property_order(client, contract):
    payload = request_for(await quote(client))
    _, first = await book(client, contract, payload)
    reordered = dict(reversed(list(payload.items())))
    reordered["expectedPrice"] = {"currency": "USD", "amount": payload["expectedPrice"]["amount"]}
    r, replay = await book(client, contract, reordered)
    assert r.status_code == 200 and replay["id"] == first["id"]


async def test_replay_succeeds_after_quote_expired_or_changed(client, contract):
    payload = request_for(await quote(client))
    _, first = await book(client, contract, payload)
    await client.post("/_control/clock", json={"advanceSeconds": 3600})
    await client.post(f"/_control/offers/{HOTEL}", json={"price": "999.00"})
    r, replay = await book(client, contract, payload)
    assert r.status_code == 200 and replay == first


async def test_same_key_different_body_is_idempotency_conflict(client, contract):
    offer = await quote(client)
    await book(client, contract, request_for(offer))
    r, err = await book(client, contract, request_for(offer, reference="other-ref"))
    assert r.status_code == 409 and err["code"] == "IDEMPOTENCY_CONFLICT"


async def test_new_key_with_used_reference_is_reference_conflict(client, contract):
    payload = request_for(await quote(client))
    _, first = await book(client, contract, payload, key="key-1")
    r, err = await book(client, contract, payload, key="key-2")
    assert r.status_code == 409 and err["code"] == "REFERENCE_CONFLICT"
    # The existing booking is discoverable through the reference.
    r = await client.get("/v1/bookings/by-reference/trip-1-hotel")
    assert contract.check(r, "GET", BY_REF)["id"] == first["id"]


@pytest.mark.parametrize("change", ["revision", "price"])
async def test_mismatched_terms_are_offer_changed(client, contract, change):
    payload = request_for(await quote(client))
    if change == "revision":
        payload["expectedOfferRevision"] = "quote-stale-v0"
    else:
        payload["expectedPrice"] = {"amount": "600.00", "currency": "USD"}
    r, err = await book(client, contract, payload)
    assert r.status_code == 409 and err["code"] == "OFFER_CHANGED"


async def test_expired_quote_is_offer_changed_and_creates_nothing(client, contract):
    payload = request_for(await quote(client))
    await client.post("/_control/clock", json={"advanceSeconds": 900})  # exactly validUntil
    r, err = await book(client, contract, payload)
    assert r.status_code == 409 and err["code"] == "OFFER_CHANGED"
    r = await client.get("/v1/bookings/by-reference/trip-1-hotel")
    assert r.status_code == 404


async def test_price_change_requires_new_terms(client, contract):
    old = await quote(client)
    await client.post(f"/_control/offers/{HOTEL}", json={"price": "710.00"})
    r, err = await book(client, contract, request_for(old))
    assert err["code"] == "OFFER_CHANGED"
    # Rejection did not consume the key or reference: accepting the new terms works.
    new = await quote(client)
    r, booking = await book(client, contract, request_for(new), key="key-2")
    assert r.status_code == 201 and booking["offer"]["price"]["amount"] == "710.00"


async def test_unavailable_offer_is_rejected(client, contract):
    payload = request_for(await quote(client, "offer-lis-hotel-belem-p2"))
    r, err = await book(client, contract, payload)
    assert r.status_code == 409 and err["code"] == "OFFER_UNAVAILABLE"


async def test_offer_becoming_unavailable_is_rejected(client, contract):
    payload = request_for(await quote(client))
    await client.post(f"/_control/offers/{HOTEL}", json={"available": False})
    r, err = await book(client, contract, payload)
    assert err["code"] == "OFFER_UNAVAILABLE"


async def test_unknown_offer_returns_404(client, contract):
    payload = request_for(await quote(client))
    payload["offerId"] = "offer-missing-p2"
    r, err = await book(client, contract, payload)
    assert r.status_code == 404 and err["code"] == "NOT_FOUND"


async def test_missing_idempotency_key_returns_400(client, contract):
    payload = request_for(await quote(client))
    r = await client.post("/v1/bookings", json=payload)
    assert r.status_code == 400
    assert contract.check(r, "POST", CREATE)["code"] == "BAD_REQUEST"


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("expectedPrice"),
    lambda p: p.update(expectedPrice={"amount": "670", "currency": "USD"}),
    lambda p: p.update(expectedPrice={"amount": "670.00", "currency": "EUR"}),
    lambda p: p.update(clientReference=""),
    lambda p: p.update(extra=True),
])
async def test_invalid_booking_body_returns_400_and_creates_nothing(client, contract, mutate):
    payload = request_for(await quote(client))
    mutate(payload)
    r, err = await book(client, contract, payload, valid=False)
    assert r.status_code == 400 and err["code"] == "BAD_REQUEST"
    assert (await client.get("/v1/bookings/booking-000001")).status_code == 404


@pytest.mark.parametrize("mode,status", [("unavailable", 503), ("rate_limited", 429)])
async def test_supplier_failure_creates_no_booking(client, contract, mode, status):
    payload = request_for(await quote(client))
    await client.post("/_control/scenario", json={"supplierId": "supplier-a", "mode": mode})
    r, _ = await book(client, contract, payload)
    assert r.status_code == status
    await client.delete("/_control/scenario")
    r, booking = await book(client, contract, payload)
    assert r.status_code == 201 and booking["id"] == "booking-000001"


async def test_pending_booking_settles_and_terminal_outcomes_do_not_reverse(client, contract):
    payload = request_for(await quote(client, "offer-par-apartment-montmartre-p2"), "trip-1-apt")
    r, booking = await book(client, contract, payload)
    assert r.status_code == 201 and booking["status"] == "pending"

    await client.post("/_control/clock", json={"advanceSeconds": 60})
    r = await client.post(f"/_control/booking/{booking['id']}/settle", json={"status": "confirmed"})
    assert r.status_code == 200

    r = await client.get(f"/v1/bookings/{booking['id']}")
    settled = contract.check(r, "GET", BY_ID)
    assert settled["status"] == "confirmed"
    assert settled["updatedAt"] == "2030-06-01T12:01:00Z"
    assert settled["createdAt"] == booking["createdAt"]

    r = await client.post(f"/_control/booking/{booking['id']}/settle", json={"status": "failed"})
    assert r.status_code == 409
    _, replay = await book(client, contract, payload)
    assert replay["status"] == "confirmed"


async def test_failed_booking_includes_failure_and_is_replayed_as_failed(client, contract):
    payload = request_for(await quote(client, "offer-lis-surf-lesson-p2"), "trip-1-surf")
    r, booking = await book(client, contract, payload)
    assert r.status_code == 201 and booking["status"] == "failed"
    assert set(booking["failure"]) == {"code", "message"}
    r, replay = await book(client, contract, payload)
    assert r.status_code == 200 and replay == booking


async def test_scenario_can_force_booking_outcome(client, contract):
    await client.post(
        "/_control/scenario", json={"supplierId": "supplier-a", "bookingOutcome": "pending"}
    )
    r, booking = await book(client, contract, request_for(await quote(client)))
    assert booking["status"] == "pending"


async def test_confirmed_booking_keeps_accepted_price_after_offer_changes(client, contract):
    _, booking = await book(client, contract, request_for(await quote(client)))
    await client.post(f"/_control/offers/{HOTEL}", json={"price": "850.00"})
    r = await client.get(f"/v1/bookings/{booking['id']}")
    assert contract.check(r, "GET", BY_ID)["offer"]["price"]["amount"] == "670.00"


async def test_bookings_keys_and_references_are_scoped_to_the_caller(make_client, contract):
    async with make_client() as alice, make_client(token="other-caller-token") as bob:
        payload = request_for(await quote(alice))
        _, booking = await book(alice, contract, payload)

        r = await bob.get(f"/v1/bookings/{booking['id']}")
        assert r.status_code == 404
        contract.check(r, "GET", BY_ID)
        r = await bob.get("/v1/bookings/by-reference/trip-1-hotel")
        assert r.status_code == 404
        contract.check(r, "GET", BY_REF)

        # Same key and reference under another caller is an independent booking intent.
        r, other = await book(bob, contract, payload)
        assert r.status_code == 201 and other["id"] != booking["id"]


async def test_concurrent_identical_requests_create_exactly_one_booking(client, contract):
    payload = request_for(await quote(client))
    responses = await asyncio.gather(*(
        client.post("/v1/bookings", json=payload, headers={"Idempotency-Key": "same"})
        for _ in range(20)
    ))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] * 19 + [201]
    ids = {contract.check(r, "POST", CREATE)["id"] for r in responses}
    assert ids == {"booking-000001"}
    assert (await client.get("/v1/bookings/booking-000002")).status_code == 404


async def test_concurrent_different_keys_same_reference_create_one_booking(client, contract):
    payload = request_for(await quote(client))
    responses = await asyncio.gather(*(
        client.post("/v1/bookings", json=payload, headers={"Idempotency-Key": f"k-{i}"})
        for i in range(10)
    ))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201] + [409] * 9
    assert all(
        contract.check(r, "POST", CREATE)["code"] == "REFERENCE_CONFLICT"
        for r in responses if r.status_code == 409
    )


async def test_lookups_of_unknown_bookings_return_404(client, contract):
    r = await client.get("/v1/bookings/booking-999999")
    assert contract.check(r, "GET", BY_ID)["code"] == "NOT_FOUND"
    r = await client.get("/v1/bookings/by-reference/never-used")
    assert contract.check(r, "GET", BY_REF)["code"] == "NOT_FOUND"
