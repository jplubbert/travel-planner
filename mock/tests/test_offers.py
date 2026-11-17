PATH = "/offers/{offerId}"
HOTEL = "offer-lis-hotel-central-p2"


async def get(client, contract, offer_id):
    r = await client.get(f"/v1/offers/{offer_id}")
    return r, contract.check(r, "GET", PATH)


async def test_get_offer_returns_current_terms(client, contract):
    r, offer = await get(client, contract, HOTEL)
    assert r.status_code == 200
    assert offer["id"] == HOTEL and offer["travelers"] == 2
    assert offer["price"] == {"amount": "670.00", "currency": "USD"}
    assert offer["available"] is True
    assert offer["validUntil"] == "2030-06-01T12:15:00Z"


async def test_get_offer_matches_search_result(client, contract):
    search = await client.post("/v1/offers/search", json={
        "category": "accommodation", "destination": "Lisbon",
        "startsOn": "2030-06-10", "endsOn": "2030-06-14", "travelers": 2,
    })
    from_search = next(o for o in search.json()["offers"] if o["id"] == HOTEL)
    _, direct = await get(client, contract, HOTEL)
    assert direct == from_search


async def test_transport_offer_includes_origin(client, contract):
    _, offer = await get(client, contract, "offer-lis-flight-lon-am-p1")
    assert offer["origin"] == "London" and offer["category"] == "transport"


async def test_unavailable_offer_is_returned_with_available_false(client, contract):
    r, offer = await get(client, contract, "offer-lis-hotel-belem-p2")
    assert r.status_code == 200 and offer["available"] is False


async def test_unknown_offer_returns_404(client, contract):
    for offer_id in ["offer-nope-p2", "offer-lis-hotel-central-p9", "x" * 200]:
        r, data = await get(client, contract, offer_id)
        assert r.status_code == 404 and data["code"] == "NOT_FOUND"


async def test_expired_quote_is_refreshed_with_new_revision_and_validity(client, contract):
    _, first = await get(client, contract, HOTEL)
    await client.post("/_control/clock", json={"advanceSeconds": 899})
    _, still = await get(client, contract, HOTEL)
    assert still["revision"] == first["revision"]

    await client.post("/_control/clock", json={"advanceSeconds": 1})  # exactly at validUntil
    _, refreshed = await get(client, contract, HOTEL)
    assert refreshed["revision"] != first["revision"]
    assert refreshed["validUntil"] == "2030-06-01T12:30:00Z"


async def test_supplier_scenarios_apply_to_offer_lookup(client, contract):
    await client.post(
        "/_control/scenario", json={"supplierId": "supplier-a", "mode": "unavailable"}
    )
    r, data = await get(client, contract, HOTEL)
    assert r.status_code == 503 and data["code"] == "PROVIDER_UNAVAILABLE"

    await client.post("/_control/scenario", json={
        "supplierId": "supplier-a", "mode": "rate_limited", "retryAfterSeconds": 3})
    r, data = await get(client, contract, HOTEL)
    assert r.status_code == 429 and r.headers["Retry-After"] == "3"

    # Other suppliers are unaffected.
    r, _ = await get(client, contract, "offer-lis-apartment-alfama-p2")
    assert r.status_code == 200


async def test_control_price_change_creates_new_revision(client, contract):
    _, before = await get(client, contract, HOTEL)
    r = await client.post(f"/_control/offers/{HOTEL}", json={"price": "699.00"})
    assert r.status_code == 200
    _, after = await get(client, contract, HOTEL)
    assert after["price"]["amount"] == "699.00"
    assert after["revision"] != before["revision"]
