"""HttpProviderClient against the real provider mock: every port operation, over HTTP."""

from datetime import date

import pytest

from app.adapters.provider_http import HttpProviderClient
from app.domain import provider as p
from app.domain.provider import BookingRequest, SearchQuery

QUERY = SearchQuery("accommodation", "Lisbon", date(2030, 6, 10), date(2030, 6, 14), 2)


@pytest.fixture
async def provider(mock_provider_url, mock_control):
    client = HttpProviderClient(f"{mock_provider_url}/v1", "travel-demo-token", 3.0)
    yield client
    await client.aclose()


def request_for(offer, reference="trip-1-hotel"):
    return BookingRequest(reference, offer.id, offer.revision, offer.price)


async def test_search_and_get_offer(provider):
    result = await provider.search(QUERY)
    assert not result.partial and result.offers
    first = result.offers[0]
    assert await provider.get_offer(first.id) == first


async def test_booking_lifecycle_and_idempotent_replay(provider):
    offer = await provider.get_offer("offer-lis-hotel-central-p2")
    booking = await provider.create_booking(request_for(offer), idempotency_key="k1")
    assert booking.status == "confirmed" and not booking.replayed
    assert booking.offer == offer

    replay = await provider.create_booking(request_for(offer), idempotency_key="k1")
    assert replay.replayed and replay.id == booking.id
    assert (await provider.get_booking(booking.id)).id == booking.id
    assert (await provider.get_booking_by_reference("trip-1-hotel")).id == booking.id


async def test_conflicts_are_typed(provider, mock_control):
    offer = await provider.get_offer("offer-lis-hotel-central-p2")
    await provider.create_booking(request_for(offer), idempotency_key="k1")
    with pytest.raises(p.ReferenceConflict):
        await provider.create_booking(request_for(offer), idempotency_key="k2")
    with pytest.raises(p.IdempotencyConflict):
        await provider.create_booking(request_for(offer, "other"), idempotency_key="k1")

    await mock_control.post("/offers/offer-lis-hotel-central-p3", json={"price": "999.00"})
    stale = await provider.get_offer("offer-lis-hotel-central-p3")
    await mock_control.post("/offers/offer-lis-hotel-central-p3", json={"price": "1000.00"})
    with pytest.raises(p.OfferChanged):
        await provider.create_booking(request_for(stale, "r3"), idempotency_key="k3")

    gone = await provider.get_offer("offer-lis-hotel-belem-p2")
    with pytest.raises(p.OfferUnavailable):
        await provider.create_booking(request_for(gone, "r4"), idempotency_key="k4")


async def test_not_found_and_supplier_failures(provider, mock_control):
    with pytest.raises(p.ProviderNotFound):
        await provider.get_booking_by_reference("never-used")
    with pytest.raises(p.ProviderNotFound):
        await provider.get_offer("offer-nope-p1")

    await mock_control.post("/scenario", json={
        "supplierId": "supplier-a", "mode": "rate_limited", "retryAfterSeconds": 4})
    with pytest.raises(p.ProviderRateLimited) as exc:
        await provider.get_offer("offer-lis-hotel-central-p2")
    assert exc.value.retry_after_seconds == 4 and exc.value.provider_request_id

    await mock_control.post("/scenario", json={"supplierId": "supplier-a", "mode": "unavailable"})
    with pytest.raises(p.ProviderUnavailable):
        await provider.get_offer("offer-lis-hotel-central-p2")


async def test_lost_booking_response_is_a_timeout_and_reconcilable(
    mock_provider_url, mock_control
):
    """The mock commits the booking and answers late; the client gives up (uncertain)."""
    impatient = HttpProviderClient(f"{mock_provider_url}/v1", "travel-demo-token", 0.3)
    patient = HttpProviderClient(f"{mock_provider_url}/v1", "travel-demo-token", 3.0)
    try:
        offer = await patient.get_offer("offer-lis-hotel-central-p2")
        await mock_control.post("/scenario", json={
            "supplierId": "supplier-a", "mode": "timeout", "delaySeconds": 2})
        with pytest.raises(p.ProviderTimeout) as exc:
            await impatient.create_booking(request_for(offer), idempotency_key="k1")
        assert exc.value.delivery_uncertain
        await mock_control.delete("/scenario")
        found = await patient.get_booking_by_reference("trip-1-hotel")
        assert found.status == "confirmed"
    finally:
        await impatient.aclose()
        await patient.aclose()
