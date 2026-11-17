from datetime import date, datetime
from decimal import Decimal

import pytest

PATH = "/offers/search"


def body(**overrides):
    base = {
        "category": "accommodation",
        "destination": "Lisbon",
        "startsOn": "2030-06-10",
        "endsOn": "2030-06-14",
        "travelers": 2,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


async def search(client, contract, payload, *, valid=True):
    if valid:
        contract.check_request("SearchRequest", payload)
    r = await client.post("/v1/offers/search", json=payload)
    return r, contract.check(r, "POST", PATH)


async def scenario(client, supplier, mode, **extra):
    r = await client.post(
        "/_control/scenario", json={"supplierId": supplier, "mode": mode, **extra}
    )
    assert r.status_code == 200


@pytest.mark.parametrize("category,extra", [
    ("accommodation", {}),
    ("activity", {}),
    ("transport", {"origin": "London"}),
])
async def test_complete_search_per_category(client, contract, category, extra):
    r, data = await search(client, contract, body(category=category, **extra))
    assert r.status_code == 200
    assert data["partial"] is False and data["warnings"] == []
    assert data["offers"], "fixtures should match"
    assert {o["category"] for o in data["offers"]} == {category}
    assert data["searchedAt"] == "2030-06-01T12:00:00Z"


@pytest.mark.parametrize("destination", ["Lisbon", "Madrid", "Paris"])
@pytest.mark.parametrize("category", ["accommodation", "activity"])
async def test_at_least_four_offers_per_category_and_destination(
    client, contract, destination, category
):
    window = {"Lisbon": ("2030-06-10", "2030-06-14"), "Madrid": ("2030-06-14", "2030-06-18"),
              "Paris": ("2030-06-18", "2030-06-22")}[destination]
    _, data = await search(client, contract, body(
        category=category, destination=destination, startsOn=window[0], endsOn=window[1]))
    assert len(data["offers"]) >= 4


async def test_offers_fit_party_and_window_and_price_covers_whole_party(client, contract):
    _, two = await search(client, contract, body(travelers=2))
    _, three = await search(client, contract, body(travelers=3))
    for offer in two["offers"]:
        assert offer["travelers"] == 2
        assert date(2030, 6, 10) <= datetime.fromisoformat(offer["startsAt"]).date()
        assert datetime.fromisoformat(offer["endsAt"]).date() <= date(2030, 6, 14)
    hotel2 = next(o for o in two["offers"] if o["id"] == "offer-lis-hotel-central-p2")
    hotel3 = next(o for o in three["offers"] if o["id"] == "offer-lis-hotel-central-p3")
    assert hotel2["price"] == {"amount": "670.00", "currency": "USD"}  # 480 + 2 * 95
    assert Decimal(hotel3["price"]["amount"]) == Decimal("765.00")


async def test_party_larger_than_capacity_is_excluded(client, contract):
    _, data = await search(client, contract, body(travelers=5))
    ids = {o["id"] for o in data["offers"]}
    assert "offer-lis-hotel-central-p5" not in ids  # max 4
    assert "offer-lis-apartment-alfama-p5" in ids  # max 6


async def test_destination_and_origin_match_case_insensitively(client, contract):
    _, lower = await search(client, contract, body(
        category="transport", destination="lisbon", origin="LONDON"))
    _, exact = await search(client, contract, body(
        category="transport", destination="Lisbon", origin="London"))
    assert [o["id"] for o in lower["offers"]] == [o["id"] for o in exact["offers"]]
    assert all(o["origin"] == "London" for o in lower["offers"])


async def test_window_excluding_offers_gives_complete_empty_result(client, contract):
    r, data = await search(client, contract, body(startsOn="2030-06-11", endsOn="2030-06-12"))
    assert r.status_code == 200
    assert data == {"offers": [], "partial": False, "warnings": [],
                    "searchedAt": "2030-06-01T12:00:00Z"}


async def test_unknown_destination_is_empty_not_an_error(client, contract):
    r, data = await search(client, contract, body(destination="Atlantis"))
    assert r.status_code == 200 and data["offers"] == [] and data["partial"] is False


async def test_unavailable_offers_are_listed_with_available_false(client, contract):
    _, data = await search(client, contract, body())
    belem = next(o for o in data["offers"] if o["id"] == "offer-lis-hotel-belem-p2")
    assert belem["available"] is False


@pytest.mark.parametrize("payload,reason", [
    (body(category="transport"), "transport without origin"),
    (body(startsOn="2030-06-15", endsOn="2030-06-14"), "startsOn after endsOn"),
    (body(startsOn="2030-02-30"), "impossible date"),
    (body(startsOn="10/06/2030"), "wrong date format"),
    (body(travelers=0), "too few travelers"),
    (body(travelers=21), "too many travelers"),
    (body(travelers="2"), "travelers as string"),
    (body(travelers=True), "travelers as boolean"),
    (body(category="cruise"), "unknown category"),
    (body(destination=""), "empty destination"),
    ({**body(), "nights": 3}, "unknown property"),
    ({**body(), "origin": None}, "null origin"),
    ({k: v for k, v in body().items() if k != "destination"}, "missing destination"),
    (body(providerId="supplier-z"), "unknown supplier"),
    (body(category="transport", origin="London", providerId="supplier-a"),
     "supplier does not offer the category"),
])
async def test_invalid_requests_return_400(client, contract, payload, reason):
    r, data = await search(client, contract, payload, valid=False)
    assert r.status_code == 400, reason
    assert data["code"] == "BAD_REQUEST"


async def test_malformed_json_returns_400(client, contract):
    r = await client.post(
        "/v1/offers/search", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400
    contract.check(r, "POST", PATH)


async def test_provider_id_limits_search_to_that_supplier(client, contract):
    _, data = await search(client, contract, body(providerId="supplier-b"))
    assert data["offers"] and {o["providerId"] for o in data["offers"]} == {"supplier-b"}


@pytest.mark.parametrize("mode,code", [
    ("unavailable", "PROVIDER_UNAVAILABLE"),
    ("timeout", "PROVIDER_TIMEOUT"),
    ("rate_limited", "RATE_LIMITED"),
])
async def test_one_failing_supplier_gives_partial_results(client, contract, mode, code):
    await scenario(client, "supplier-b", mode)
    r, data = await search(client, contract, body())
    assert r.status_code == 200
    assert data["partial"] is True
    assert data["warnings"] == [
        {"providerId": "supplier-b", "code": code, "message": data["warnings"][0]["message"]}
    ]
    assert data["offers"] and {o["providerId"] for o in data["offers"]} == {"supplier-a"}


async def test_all_suppliers_failing_returns_503(client, contract):
    await scenario(client, "supplier-a", "unavailable")
    await scenario(client, "supplier-b", "rate_limited")
    r, data = await search(client, contract, body())
    assert r.status_code == 503 and data["code"] == "PROVIDER_UNAVAILABLE"
    assert {w["providerId"] for w in data["details"]["warnings"]} == {"supplier-a", "supplier-b"}


async def test_rate_limited_single_supplier_query_returns_429(client, contract):
    await scenario(client, "supplier-b", "rate_limited", retryAfterSeconds=7)
    r, data = await search(client, contract, body(providerId="supplier-b"))
    assert r.status_code == 429 and data["code"] == "RATE_LIMITED"
    assert r.headers["Retry-After"] == "7"


@pytest.mark.parametrize("mode", ["unavailable", "timeout"])
async def test_failing_single_supplier_query_returns_503(client, contract, mode):
    await scenario(client, "supplier-b", mode)
    r, data = await search(client, contract, body(providerId="supplier-b"))
    assert r.status_code == 503 and data["code"] == "PROVIDER_UNAVAILABLE"


async def test_search_refreshes_expired_quotes_with_new_revision(client, contract):
    _, first = await search(client, contract, body())
    hotel = next(o for o in first["offers"] if o["id"] == "offer-lis-hotel-central-p2")
    assert hotel["validUntil"] == "2030-06-01T12:15:00Z"

    _, same = await search(client, contract, body())
    again = next(o for o in same["offers"] if o["id"] == hotel["id"])
    assert again["revision"] == hotel["revision"]  # still valid: no new revision

    await client.post("/_control/clock", json={"advanceSeconds": 900})
    _, later = await search(client, contract, body())
    refreshed = next(o for o in later["offers"] if o["id"] == hotel["id"])
    assert refreshed["revision"] != hotel["revision"]
    assert refreshed["validUntil"] == "2030-06-01T12:30:00Z"
    assert refreshed["price"] == hotel["price"]
