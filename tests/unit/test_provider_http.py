"""HttpProviderClient error mapping, deadlines and diagnostics, with a scripted transport."""

import asyncio
import json
import logging
import time
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.adapters.provider_http import HttpProviderClient
from app.domain import provider as p
from app.domain.provider import BookingRequest, SearchQuery

QUERY = SearchQuery("accommodation", "Lisbon", date(2030, 6, 10), date(2030, 6, 14), 2)
BOOKING = BookingRequest("ref-1", "offer-a/b c", "rev-1", Decimal("670"))
OFFER = {
    "id": "offer-x-p2", "providerId": "supplier-a", "category": "accommodation",
    "title": "Hotel", "description": "d", "destination": "Lisbon",
    "startsAt": "2030-06-10T15:00:00+01:00", "endsAt": "2030-06-14T11:00:00+01:00",
    "travelers": 2, "price": {"amount": "670.00", "currency": "USD"}, "revision": "rev-1",
    "available": True, "validUntil": "2030-06-01T12:15:00Z", "restrictions": ["r"],
}


def client(handler, timeout=1.0) -> HttpProviderClient:
    return HttpProviderClient("http://provider.test/v1", "secret-token", timeout,
                              transport=httpx.MockTransport(handler))


def error(status, code, *, headers=None, request_id="prov-req-1", details=None):
    body = {"code": code, "message": f"{code} happened", "requestId": request_id}
    if details:
        body["details"] = details
    return lambda request: httpx.Response(status, json=body, headers=headers)


@pytest.mark.parametrize("status,code,expected", [
    (400, "BAD_REQUEST", p.ProviderBadRequest),
    (401, "UNAUTHORIZED", p.ProviderAuthFailed),
    (404, "NOT_FOUND", p.ProviderNotFound),
    (409, "OFFER_CHANGED", p.OfferChanged),
    (409, "OFFER_UNAVAILABLE", p.OfferUnavailable),
    (409, "IDEMPOTENCY_CONFLICT", p.IdempotencyConflict),
    (409, "REFERENCE_CONFLICT", p.ReferenceConflict),
    (429, "RATE_LIMITED", p.ProviderRateLimited),
    (503, "PROVIDER_UNAVAILABLE", p.ProviderUnavailable),
    (500, "PROVIDER_UNAVAILABLE", p.ProviderProtocolError),  # not in the contract
])
async def test_status_codes_map_to_distinct_domain_errors(status, code, expected):
    c = client(error(status, code))
    with pytest.raises(p.ProviderError) as exc:
        await c.create_booking(BOOKING, idempotency_key="k")
    assert type(exc.value) is expected
    assert exc.value.provider_request_id == "prov-req-1"
    await c.aclose()


def test_every_mapped_error_type_is_distinct():
    types = [p.ProviderBadRequest, p.ProviderAuthFailed, p.ProviderNotFound, p.OfferChanged,
             p.OfferUnavailable, p.IdempotencyConflict, p.ReferenceConflict,
             p.ProviderRateLimited, p.ProviderUnavailable, p.ProviderUnreachable,
             p.ProviderTimeout, p.ProviderTransportError, p.ProviderProtocolError]
    assert len({t.code for t in types}) == len(types)


@pytest.mark.parametrize("error_type,uncertain", [
    (p.ProviderBadRequest, False), (p.ProviderRateLimited, False),
    (p.ProviderUnavailable, False), (p.ProviderUnreachable, False),
    (p.OfferChanged, False), (p.ProviderTimeout, True), (p.ProviderTransportError, True),
    (p.ProviderProtocolError, True),
])
def test_delivery_uncertainty_matches_the_contract(error_type, uncertain):
    """ADR 0003: only answers that prove nothing happened are 'certain'."""
    assert error_type.delivery_uncertain is uncertain


async def test_rate_limit_carries_retry_after():
    c = client(error(429, "RATE_LIMITED", headers={"Retry-After": "7"}))
    with pytest.raises(p.ProviderRateLimited) as exc:
        await c.search(QUERY)
    assert exc.value.retry_after_seconds == 7


async def test_unavailable_search_carries_supplier_warnings():
    warnings = [{"providerId": "supplier-a", "code": "PROVIDER_TIMEOUT", "message": "slow"}]
    c = client(error(503, "PROVIDER_UNAVAILABLE", details={"warnings": warnings}))
    with pytest.raises(p.ProviderUnavailable) as exc:
        await c.search(QUERY)
    assert exc.value.warnings == (p.SupplierWarning("supplier-a", "PROVIDER_TIMEOUT", "slow"),)


@pytest.mark.parametrize("response", [
    httpx.Response(503, text="<html>gateway</html>"),  # not a contract Error body
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"offers": [{"id": "x"}], "partial": False, "warnings": [],
                              "searchedAt": "2030-06-01T12:00:00Z"}),
    httpx.Response(200, json={"offers": [{**OFFER, "price": {"amount": "1", "currency": "EUR"}}],
                              "partial": False, "warnings": [],
                              "searchedAt": "2030-06-01T12:00:00Z"}),
])
async def test_responses_outside_the_contract_are_protocol_errors(response):
    c = client(lambda request: response)
    with pytest.raises(p.ProviderProtocolError):
        await c.search(QUERY)


@pytest.mark.parametrize("raised,expected", [
    (httpx.ConnectError("refused"), p.ProviderUnreachable),
    (httpx.ConnectTimeout("slow connect"), p.ProviderUnreachable),
    (httpx.ReadTimeout("slow read"), p.ProviderTimeout),
    (httpx.ReadError("reset"), p.ProviderTransportError),
    (httpx.RemoteProtocolError("closed"), p.ProviderTransportError),
])
async def test_transport_failures_are_mapped(raised, expected):
    def handler(request):
        raise raised

    c = client(handler)
    with pytest.raises(expected):
        await c.get_offer("offer-x-p2")


async def test_overall_deadline_bounds_a_slow_provider():
    async def slow(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    c = client(slow, timeout=0.2)
    started = time.monotonic()
    with pytest.raises(p.ProviderTimeout):
        await c.search(QUERY)
    assert time.monotonic() - started < 0.5


async def test_requests_follow_the_contract():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=OFFER)
        return httpx.Response(201, json={
            "id": "booking-1", "clientReference": "ref-1", "offer": OFFER, "status": "pending",
            "createdAt": "2030-06-01T12:00:00Z", "updatedAt": "2030-06-01T12:00:00Z"})

    c = client(handler)
    booking = await c.create_booking(BOOKING, idempotency_key="intent-1")
    request = seen[0]
    assert request.url.path == "/v1/bookings"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["Idempotency-Key"] == "intent-1"
    assert json.loads(request.content) == {
        "clientReference": "ref-1", "offerId": "offer-a/b c", "expectedOfferRevision": "rev-1",
        "expectedPrice": {"amount": "670.00", "currency": "USD"}}
    assert booking.status == "pending" and booking.replayed is False

    await c.get_offer("offer-a/b c")  # path segment is escaped
    assert seen[1].url.raw_path == b"/v1/offers/offer-a%2Fb%20c"


async def test_replay_is_flagged():
    c = client(lambda r: httpx.Response(200, json={
        "id": "booking-1", "clientReference": "ref-1", "offer": OFFER, "status": "confirmed",
        "createdAt": "2030-06-01T12:00:00Z", "updatedAt": "2030-06-01T12:00:00Z"}))
    booking = await c.create_booking(BOOKING, idempotency_key="intent-1")
    assert booking.replayed is True and booking.status == "confirmed"


async def test_logs_carry_provider_request_id_but_never_the_token(caplog):
    caplog.set_level(logging.INFO, logger="app.provider")
    ok = client(lambda r: httpx.Response(200, json=OFFER, headers={"X-Request-Id": "hdr-9"}))
    await ok.get_offer("offer-x-p2")
    failing = client(error(409, "OFFER_CHANGED", request_id="prov-req-42"))
    with pytest.raises(p.OfferChanged):
        await failing.create_booking(BOOKING, idempotency_key="k")
    text = caplog.text
    assert "provider_request_id=hdr-9" in text
    assert "provider_request_id=prov-req-42" in text and "OFFER_CHANGED" in text
    assert "secret-token" not in text
