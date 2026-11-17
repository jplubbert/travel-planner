import pytest

SEARCH = {
    "category": "accommodation",
    "destination": "Lisbon",
    "startsOn": "2030-06-10",
    "endsOn": "2030-06-14",
    "travelers": 2,
}

OPERATIONS = [
    ("POST", "/offers/search", "/v1/offers/search", SEARCH),
    ("GET", "/offers/{offerId}", "/v1/offers/offer-lis-hotel-central-p2", None),
    ("POST", "/bookings", "/v1/bookings", {}),
    ("GET", "/bookings/by-reference/{clientReference}", "/v1/bookings/by-reference/x", None),
    ("GET", "/bookings/{bookingId}", "/v1/bookings/booking-000001", None),
]


@pytest.mark.parametrize("method,template,url,body", OPERATIONS)
@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_every_operation_requires_a_valid_bearer_token(
    make_client, contract, method, template, url, body, token
):
    async with make_client(token=token) as c:
        r = await c.request(method, url, json=body, headers={"Idempotency-Key": "k"})
    assert r.status_code == 401
    assert contract.check(r, method, template)["code"] == "UNAUTHORIZED"


async def test_non_bearer_scheme_is_rejected(make_client, contract):
    async with make_client(token=None) as c:
        r = await c.post(
            "/v1/offers/search", json=SEARCH, headers={"Authorization": "Basic dHJhdmVs"}
        )
    assert r.status_code == 401
    contract.check(r, "POST", "/offers/search")


async def test_errors_carry_request_id_matching_header(client, contract):
    r = await client.get("/v1/offers/offer-does-not-exist-p1")
    body = contract.check(r, "GET", "/offers/{offerId}")
    assert body["requestId"] == r.headers["X-Request-Id"]


async def test_control_endpoints_are_not_under_the_contract_prefix(client):
    r = await client.get("/v1/_control/clock")
    assert r.status_code == 404
