"""US-07 (confirm current terms before booking) and US-08 (uncertain and partial bookings),
against PostgreSQL and the provider mock over real HTTP. Failure scenarios are driven only
through the mock's /_control endpoints and by wrapping the real HTTP client."""

import asyncio
import contextlib
from decimal import Decimal
from typing import Any

import pytest

from app.domain import provider as p

HOTEL = "offer-lis-hotel-central-p2"  # supplier-a, confirms immediately, 670.00
SURF = "offer-lis-surf-lesson-p2"  # supplier-c, accepted then failed
APARTMENT = "offer-par-apartment-montmartre-p2"  # supplier-b, stays pending until settled


class WrappedProvider:
    """The real HTTP client with hooks around create_booking, to simulate a process dying
    or terms changing at precise moments."""

    def __init__(self, inner: Any, before: Any = None, after: Any = None) -> None:
        self.inner, self.before, self.after = inner, before, after

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def create_booking(self, request: Any, *, idempotency_key: str) -> Any:
        if self.before:
            await self.before()
        result = await self.inner.create_booking(request, idempotency_key=idempotency_key)
        if self.after:
            await self.after()
        return result


async def add_item(client, trip, offer_id):
    version = (await client.get(f"/trips/{trip['id']}")).json()["version"]
    r = await client.post(f"/trips/{trip['id']}/itinerary/items",
                          json={"version": version, "offerId": offer_id})
    assert r.status_code == 201, r.text
    return next(i for i in r.json()["items"] if i["offerId"] == offer_id)


def book(client, trip, item, *, revision=None, amount=None):
    return client.post(
        f"/trips/{trip['id']}/itinerary/items/{item['itemId']}/bookings",
        json={"expectedRevision": revision or item["quote"]["revision"],
              "expectedPrice": {"amount": amount or item["quote"]["price"]["amount"],
                                "currency": "USD"}})


async def bookings(client, trip):
    r = await client.get(f"/trips/{trip['id']}/bookings")
    assert r.status_code == 200, r.text
    return r.json()


async def provider_bookings(provider_client, upto=5):
    """Ids of the bookings the provider holds (the mock numbers them sequentially)."""
    found = []
    for n in range(1, upto + 1):
        with contextlib.suppress(p.ProviderNotFound):
            found.append(await provider_client.get_booking(f"booking-{n:06d}"))
    return found


async def scenario(mock_control, supplier, mode, **extra):
    (await mock_control.post("/scenario", json={"supplierId": supplier, "mode": mode,
                                                **extra})).raise_for_status()


async def expire_rate_limit(app):
    """Ends the shared provider rate limit now instead of waiting for it."""
    from sqlalchemy import text

    async with app.state.sessions() as session:
        await session.execute(text("UPDATE provider_rate_limits SET not_before = now()"))
        await session.commit()


def failing_post(make_app, provider_client, mock_control, mode, **extra):
    """An app whose booking POST hits a supplier failure, while the offer re-check before
    it still succeeds (the scenario is switched on right before the POST)."""
    async def fail_now():
        await scenario(mock_control, "supplier-a", mode, **extra)
    return make_app(provider=WrappedProvider(provider_client, before=fail_now))


def lost_response_app(make_app, mock_control, mock_provider_url):
    """An app (0.5s provider timeout) whose booking POST is accepted by the provider but
    answered after 2s, so the response is lost."""
    from app.adapters.provider_http import HttpProviderClient

    inner = HttpProviderClient(f"{mock_provider_url}/v1", "travel-demo-token", 0.5)

    async def slow_now():
        await scenario(mock_control, "supplier-a", "timeout", delaySeconds=2)
    return make_app(provider=WrappedProvider(inner, before=slow_now))


# --- US-07: explicit action, current terms checked first ----------------------------------


async def test_editor_books_with_reviewed_terms_and_the_accepted_offer_is_kept(
    api, shared_trip, mock_control, provider_client
):
    item = await add_item(api.as_("beto"), shared_trip, HOTEL)
    r = await book(api.as_("beto"), shared_trip, item)
    assert r.status_code == 201
    b = r.json()
    assert b["status"] == "confirmed" and b["booked"] is True
    assert b["acceptedPrice"] == {"amount": "670.00", "currency": "USD"}
    assert b["acceptedOffer"]["id"] == HOTEL
    assert b["acceptedOffer"]["revision"] == item["quote"]["revision"]
    assert b["providerBookingId"] == "booking-000001" and b["requestedBy"] == "beto"
    assert b["nextStep"].startswith("Booked")
    [listed] = await bookings(api.as_("carla"), shared_trip)  # viewers can see bookings
    assert listed == b
    [held] = await provider_bookings(provider_client)
    assert held.client_reference == b["clientReference"]


async def test_changed_price_is_not_sent_and_the_user_sees_the_new_terms(
    api, shared_trip, mock_control, provider_client
):
    ana = api.as_("ana")
    item = await add_item(ana, shared_trip, HOTEL)
    await mock_control.post(f"/offers/{HOTEL}", json={"price": "710.00"})

    r = await book(ana, shared_trip, item)  # terms as reviewed: 670.00
    assert r.status_code == 409 and r.json()["code"] == "TERMS_CHANGED"
    current = r.json()["details"]["currentTerms"]
    assert current["price"]["amount"] == "710.00" and current["revision"] != \
        item["quote"]["revision"]
    assert r.json()["details"]["reviewed"]["price"] == "670.00"
    assert await bookings(ana, shared_trip) == []
    assert await provider_bookings(provider_client) == []  # nothing reached the provider

    # The user confirms the new terms explicitly.
    r = await book(ana, shared_trip, item, revision=current["revision"], amount="710.00")
    assert r.status_code == 201 and r.json()["status"] == "confirmed"
    assert r.json()["acceptedPrice"]["amount"] == "710.00"


async def test_unavailable_option_is_never_presented_as_booked(
    api, shared_trip, mock_control, provider_client
):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    await mock_control.post(f"/offers/{HOTEL}", json={"available": False})
    r = await book(api.as_("ana"), shared_trip, item)
    assert r.status_code == 409 and r.json()["code"] == "OFFER_UNAVAILABLE"
    assert r.json()["details"]["currentTerms"]["available"] is False
    assert await bookings(api.as_("ana"), shared_trip) == []
    assert await provider_bookings(provider_client) == []


async def test_terms_changing_after_the_check_end_rejected_with_the_new_terms(
    api, make_app, shared_trip, mock_control, provider_client
):
    async def price_changes_now():
        await mock_control.post(f"/offers/{HOTEL}", json={"price": "730.00"})

    racy = make_app(provider=WrappedProvider(provider_client, before=price_changes_now))
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    r = await book(api.as_("ana", app=racy), shared_trip, item)
    assert r.status_code == 201
    b = r.json()
    assert b["status"] == "rejected" and b["booked"] is False
    assert b["failure"]["code"] == "OFFER_CHANGED"
    assert b["currentTerms"]["price"]["amount"] == "730.00"
    assert "730.00" in b["nextStep"] and "Not booked" in b["nextStep"]
    assert await provider_bookings(provider_client) == []

    # A rejected attempt is not active: the user may book again with the new terms.
    r = await book(api.as_("ana"), shared_trip, item,
                   revision=b["currentTerms"]["revision"], amount="730.00")
    assert r.status_code == 201 and r.json()["status"] == "confirmed"


@pytest.mark.parametrize("body,field", [
    ({"expectedPrice": {"amount": "670.00", "currency": "USD"}}, "expectedRevision"),
    ({"expectedRevision": "r"}, "expectedPrice"),
    ({"expectedRevision": "r", "expectedPrice": {"amount": "670.001", "currency": "USD"}},
     "expectedPrice.amount"),
    ({"expectedRevision": "r", "expectedPrice": {"amount": "670.00", "currency": "EUR"}},
     "expectedPrice"),
])
async def test_booking_requires_the_reviewed_terms(api, shared_trip, mock_control, body, field):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    r = await api.as_("ana").post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/bookings", json=body)
    assert r.status_code == 422
    assert field in [f["field"] for f in r.json()["details"]["fields"]]


# --- US-08: one active attempt, no duplicates -------------------------------------------


async def test_double_click_creates_one_attempt_and_one_provider_booking(
    api, shared_trip, mock_control, provider_client
):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    r1, r2 = await asyncio.gather(book(api.as_("ana"), shared_trip, item),
                                  book(api.as_("beto"), shared_trip, item))
    assert sorted([r1.status_code, r2.status_code]) == [201, 409]
    loser = r1 if r1.status_code == 409 else r2
    assert loser.json()["code"] in ("BOOKING_IN_PROGRESS", "ITEM_ALREADY_BOOKED")
    assert len(await bookings(api.as_("ana"), shared_trip)) == 1
    assert len(await provider_bookings(provider_client)) == 1


async def test_a_booked_item_cannot_be_booked_again(api, shared_trip, mock_control):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    first = (await book(api.as_("ana"), shared_trip, item)).json()
    r = await book(api.as_("ana"), shared_trip, item)
    assert r.status_code == 409 and r.json()["code"] == "ITEM_ALREADY_BOOKED"
    assert r.json()["details"]["bookingId"] == first["bookingId"]


@pytest.mark.parametrize("mode,code", [("rate_limited", "PROVIDER_RATE_LIMITED"),
                                       ("unavailable", "PROVIDER_UNAVAILABLE")])
async def test_not_accepted_goes_back_to_draft_and_retry_reuses_the_key(
    api, make_app, shared_trip, mock_control, provider_client, mode, code
):
    ana = api.as_("ana")
    item = await add_item(ana, shared_trip, HOTEL)
    failing = failing_post(make_app, provider_client, mock_control, mode, retryAfterSeconds=7)
    b = (await book(api.as_("ana", app=failing), shared_trip, item)).json()
    assert b["status"] == "draft" and b["booked"] is False and b["canRetry"] is True
    assert b["lastError"]["code"] == code and b["lastError"]["providerRequestId"]
    if mode == "rate_limited":
        assert b["lastError"]["retryAfterSeconds"] == 7 and "7 seconds" in b["nextStep"]
    assert "cannot create a duplicate" in b["nextStep"]
    assert await provider_bookings(provider_client) == []

    # A new attempt is refused while the draft exists: retry the same one.
    await mock_control.delete("/scenario")
    r = await book(ana, shared_trip, item)
    assert r.status_code == 409 and r.json()["code"] == "BOOKING_IN_PROGRESS"

    await mock_control.delete("/scenario")
    if mode == "rate_limited":
        # Within the 7 s the provider asked for, the retry is refused at once without
        # calling it (shared rate limit); once they pass, it goes through.
        r = await ana.post(f"/trips/{shared_trip['id']}/bookings/{b['bookingId']}/retry")
        waiting = r.json()
        assert waiting["status"] == "draft" and waiting["canRetry"] is True
        assert waiting["lastError"]["code"] == "PROVIDER_RATE_LIMITED"
        assert 1 <= waiting["lastError"]["retryAfterSeconds"] <= 7
        assert waiting["lastError"]["providerRequestId"] is None  # never reached it
        assert await provider_bookings(provider_client) == []
        await expire_rate_limit(api.app)
    r = await ana.post(f"/trips/{shared_trip['id']}/bookings/{b['bookingId']}/retry")
    assert r.status_code == 200
    retried = r.json()
    assert retried["status"] == "confirmed" and retried["bookingId"] == b["bookingId"]
    assert retried["clientReference"] == b["clientReference"]
    [held] = await provider_bookings(provider_client)
    assert held.client_reference == b["clientReference"]


async def test_simultaneous_retries_send_once(api, make_app, shared_trip, mock_control,
                                             provider_client):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    failing = failing_post(make_app, provider_client, mock_control, "unavailable")
    b = (await book(api.as_("ana", app=failing), shared_trip, item)).json()
    assert b["status"] == "draft"
    await mock_control.delete("/scenario")
    url = f"/trips/{shared_trip['id']}/bookings/{b['bookingId']}/retry"
    r1, r2 = await asyncio.gather(api.as_("ana").post(url), api.as_("beto").post(url))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409]
    assert len(await provider_bookings(provider_client)) == 1


async def test_only_drafts_can_be_retried(api, shared_trip, mock_control):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    b = (await book(api.as_("ana"), shared_trip, item)).json()
    r = await api.as_("ana").post(f"/trips/{shared_trip['id']}/bookings/{b['bookingId']}/retry")
    assert r.status_code == 409 and r.json()["code"] == "BOOKING_NOT_RETRYABLE"


# --- US-08: lost responses and interruptions --------------------------------------------


async def test_lost_response_is_unknown_then_reconciled_to_a_single_booking(
    api, make_app, mock_provider_url, shared_trip, mock_control, provider_client, make_worker
):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    # The provider commits the booking but answers after 2s; the app waits 0.5s.
    impatient = lost_response_app(make_app, mock_control, mock_provider_url)
    b = (await book(api.as_("ana", app=impatient), shared_trip, item)).json()
    assert b["status"] == "unknown" and b["booked"] is False
    assert b["lastError"]["code"] == "PROVIDER_TIMEOUT"
    assert "Do not book this option again" in b["nextStep"]

    # Booking again while unknown is refused, so the user cannot create a duplicate.
    r = await book(api.as_("ana"), shared_trip, item)
    assert r.status_code == 409 and r.json()["code"] == "BOOKING_IN_PROGRESS"

    await mock_control.delete("/scenario")
    assert await make_worker().run_once() == 1
    [after] = await bookings(api.as_("ana"), shared_trip)
    assert after["status"] == "confirmed" and after["providerBookingId"] == "booking-000001"
    assert after["acceptedPrice"]["amount"] == "670.00"
    assert len(await provider_bookings(provider_client)) == 1  # exactly one at the provider


async def test_unknown_never_becomes_success_while_the_provider_cannot_answer(
    api, make_app, mock_provider_url, shared_trip, mock_control, make_worker
):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    lossy = lost_response_app(make_app, mock_control, mock_provider_url)
    await book(api.as_("ana", app=lossy), shared_trip, item)
    await scenario(mock_control, "supplier-a", "unavailable")  # lookups now fail with 503

    worker = make_worker()
    for _ in range(3):
        await worker.run_once()
    [b] = await bookings(api.as_("ana"), shared_trip)
    assert b["status"] == "unknown"
    assert b["lastError"]["code"] == "PROVIDER_UNAVAILABLE"

    await mock_control.delete("/scenario")
    await worker.run_once()
    assert (await bookings(api.as_("ana"), shared_trip))[0]["status"] == "confirmed"


async def test_process_dies_after_sending(
    api, make_app, shared_trip, mock_control, provider_client, make_worker
):
    async def killed():
        raise RuntimeError("process killed after the request was sent")

    dying = make_app(provider=WrappedProvider(provider_client, after=killed))
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    r = await book(api.as_("ana", app=dying), shared_trip, item)
    assert r.status_code == 500
    [b] = await bookings(api.as_("ana"), shared_trip)
    assert b["status"] == "submitted"  # persisted before sending, never assumed booked

    worker = make_worker()  # start-up sweep: stale submissions become unknown
    await worker.run_once()
    [b] = await bookings(api.as_("ana"), shared_trip)
    assert b["status"] == "confirmed"
    assert len(await provider_bookings(provider_client)) == 1


async def test_process_dies_before_sending(
    api, make_app, shared_trip, mock_control, provider_client, make_worker
):
    async def killed():
        raise RuntimeError("process killed before the request was sent")

    dying = make_app(provider=WrappedProvider(provider_client, before=killed))
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    await book(api.as_("ana", app=dying), shared_trip, item)
    assert await provider_bookings(provider_client) == []

    # Not found by reference; after the grace period the same request is re-sent with the
    # same Idempotency-Key, so a late original could never be duplicated.
    await make_worker().run_once()
    [b] = await bookings(api.as_("ana"), shared_trip)
    assert b["status"] == "confirmed"
    [held] = await provider_bookings(provider_client)
    assert held.client_reference == b["clientReference"]

    # The re-send used the key stored before the crash: replaying it with that key returns
    # the same provider booking instead of creating another.
    from sqlalchemy import text

    from app.domain.provider import BookingRequest
    async with dying.state.sessions() as session:
        key = (await session.execute(text("SELECT idempotency_key FROM bookings"))).scalar_one()
    replay = await provider_client.create_booking(
        BookingRequest(b["clientReference"], HOTEL, b["expectedRevision"],
                       Decimal(b["expectedPrice"]["amount"])), idempotency_key=key)
    assert replay.replayed and replay.id == held.id


async def test_unknown_waits_for_the_grace_period_before_resending(
    api, make_app, shared_trip, mock_control, provider_client, make_worker
):
    async def killed():
        raise RuntimeError("killed")

    dying = make_app(provider=WrappedProvider(provider_client, before=killed))
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    await book(api.as_("ana", app=dying), shared_trip, item)
    patient = make_worker(unknown_grace_seconds=3600)
    await patient.run_once()
    [b] = await bookings(api.as_("ana"), shared_trip)
    assert b["status"] == "unknown"  # not found yet, and too early to re-send
    assert await provider_bookings(provider_client) == []


async def test_worker_leaves_recent_submissions_alone(
    api, make_app, shared_trip, mock_control, provider_client, make_worker
):
    async def killed():
        raise RuntimeError("killed")

    dying = make_app(provider=WrappedProvider(provider_client, before=killed))
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    await book(api.as_("ana", app=dying), shared_trip, item)
    assert await make_worker(submitted_stale_seconds=3600).run_once() == 0
    assert (await bookings(api.as_("ana"), shared_trip))[0]["status"] == "submitted"


# --- US-08: pending, failed, and partial outcomes -----------------------------------------


@pytest.mark.parametrize("outcome", ["confirmed", "failed"])
async def test_pending_booking_resolves_through_the_worker(
    api, shared_trip, mock_control, make_worker, outcome
):
    item = await add_item(api.as_("ana"), shared_trip, APARTMENT)
    b = (await book(api.as_("ana"), shared_trip, item)).json()
    assert b["status"] == "pending" and b["booked"] is False
    assert "checks automatically" in b["nextStep"]

    worker = make_worker()
    await worker.run_once()
    assert (await bookings(api.as_("ana"), shared_trip))[0]["status"] == "pending"

    # A failed status check keeps it pending (it was accepted) and records why.
    await scenario(mock_control, "supplier-b", "unavailable")
    await worker.run_once()
    [still] = await bookings(api.as_("ana"), shared_trip)
    assert still["status"] == "pending"
    assert still["lastError"]["code"] == "PROVIDER_UNAVAILABLE"
    await mock_control.delete("/scenario")

    await mock_control.post(f"/booking/{b['providerBookingId']}/settle",
                            json={"status": outcome})
    await worker.run_once()
    [after] = await bookings(api.as_("ana"), shared_trip)
    assert after["status"] == outcome
    assert after["acceptedPrice"] == b["acceptedPrice"]  # accepted snapshot unchanged
    if outcome == "failed":
        assert after["failure"]["code"] and "new booking" in after["nextStep"]


async def test_one_confirmed_and_one_failed_are_both_visible_with_next_steps(
    api, shared_trip, mock_control
):
    ana = api.as_("ana")
    hotel = await add_item(ana, shared_trip, HOTEL)
    surf = await add_item(ana, shared_trip, SURF)
    assert (await book(ana, shared_trip, hotel)).json()["status"] == "confirmed"
    failed = (await book(ana, shared_trip, surf)).json()
    assert failed["status"] == "failed" and failed["providerBookingId"]  # it was accepted
    assert failed["failure"] == {"code": "SUPPLIER_REJECTED",
                                 "message": failed["failure"]["message"]}

    listed = {b["offerId"]: b for b in await bookings(api.as_("carla"), shared_trip)}
    assert listed[HOTEL]["status"] == "confirmed" and listed[HOTEL]["booked"] is True
    assert listed[SURF]["status"] == "failed" and listed[SURF]["booked"] is False
    assert "start a new booking" in listed[SURF]["nextStep"]

    costs = (await ana.get(f"/trips/{shared_trip['id']}/costs")).json()
    basis = {line["title"]: line["basis"] for line in costs["lines"]}
    assert basis == {hotel["title"]: "confirmed", surf["title"]: "estimate"}

    # Next step for the failed one: a new attempt is allowed and the confirmed one is kept.
    r = await book(ana, shared_trip, surf)
    assert r.status_code == 201
    assert listed[HOTEL] == next(b for b in await bookings(ana, shared_trip)
                                 if b["offerId"] == HOTEL)


async def test_item_with_an_unresolved_booking_cannot_be_removed(api, shared_trip, mock_control):
    ana = api.as_("ana")
    item = await add_item(ana, shared_trip, APARTMENT)
    await book(ana, shared_trip, item)
    version = (await ana.get(f"/trips/{shared_trip['id']}")).json()["version"]
    r = await ana.delete(f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}",
                         params={"version": version})
    assert r.status_code == 409 and r.json()["code"] == "ITEM_HAS_BOOKING"


# --- Permissions ---------------------------------------------------------------------------


async def test_viewers_cannot_book_or_retry(api, make_app, shared_trip, mock_control,
                                           provider_client):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    failing = failing_post(make_app, provider_client, mock_control, "unavailable")
    draft = (await book(api.as_("ana", app=failing), shared_trip, item)).json()
    await mock_control.delete("/scenario")
    carla = api.as_("carla")
    assert (await book(carla, shared_trip, item)).status_code == 403
    r = await carla.post(f"/trips/{shared_trip['id']}/bookings/{draft['bookingId']}/retry")
    assert r.status_code == 403
    assert (await bookings(api.as_("ana"), shared_trip))[0]["status"] == "draft"


async def test_nonparticipants_get_404_everywhere(api, shared_trip, mock_control):
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    b = (await book(api.as_("ana"), shared_trip, item)).json()
    dani, base = api.as_("dani"), f"/trips/{shared_trip['id']}"
    for r in (await book(dani, shared_trip, item),
              await dani.get(f"{base}/bookings"),
              await dani.get(f"{base}/bookings/{b['bookingId']}"),
              await dani.post(f"{base}/bookings/{b['bookingId']}/retry")):
        assert r.status_code == 404 and r.json()["code"] == "TRIP_NOT_FOUND"


async def test_bookings_of_another_trip_are_not_reachable(
    api, shared_trip, trip_payload, mock_control
):
    other = (await api.as_("ana").post("/trips", json=trip_payload)).json()
    item = await add_item(api.as_("ana"), other, HOTEL)
    b = (await book(api.as_("ana"), other, item)).json()
    r = await api.as_("ana").get(f"/trips/{shared_trip['id']}/bookings/{b['bookingId']}")
    assert r.status_code == 404 and r.json()["code"] == "BOOKING_NOT_FOUND"


async def test_retry_sends_the_same_idempotency_key(
    api, make_app, shared_trip, mock_control, provider_client
):
    sent: list[str] = []

    class Recording:
        def __init__(self, fail_first: bool) -> None:
            self.fail_first = fail_first

        def __getattr__(self, name):
            return getattr(provider_client, name)

        async def create_booking(self, request, *, idempotency_key):
            sent.append(idempotency_key)
            if self.fail_first:
                await scenario(mock_control, "supplier-a", "unavailable")
            return await provider_client.create_booking(request,
                                                        idempotency_key=idempotency_key)

    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    first = make_app(provider=Recording(fail_first=True))
    b = (await book(api.as_("ana", app=first), shared_trip, item)).json()
    assert b["status"] == "draft"
    await mock_control.delete("/scenario")
    second = make_app(provider=Recording(fail_first=False))
    r = await api.as_("ana", app=second).post(
        f"/trips/{shared_trip['id']}/bookings/{b['bookingId']}/retry")
    assert r.json()["status"] == "confirmed"
    assert len(sent) == 2 and sent[0] == sent[1]


async def test_reconciliation_does_not_take_over_a_submission_still_in_flight(
    api, make_app, app, shared_trip, mock_control, provider_client
):
    """Even if a sync job exists for a fresh ``submitted`` booking, the step leaves it alone
    until it is older than the stale threshold (its sender may still be waiting)."""
    from uuid import UUID

    from app.adapters.clock import SystemClock
    from app.adapters.sqlalchemy.uow import SqlUnitOfWork
    from app.domain.reservations import ReconcilePolicy, sync_booking

    async def killed():
        raise RuntimeError("killed")

    dying = make_app(provider=WrappedProvider(provider_client, before=killed))
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    await book(api.as_("ana", app=dying), shared_trip, item)
    [b] = await bookings(api.as_("ana"), shared_trip)
    async with SqlUnitOfWork(app.state.sessions) as uow:
        result = await sync_booking(uow, provider_client, SystemClock(), UUID(b["bookingId"]),
                                    ReconcilePolicy(submitted_stale_seconds=3600))
    assert not result.done and result.retry_in > 0
    assert (await bookings(api.as_("ana"), shared_trip))[0]["status"] == "submitted"


async def test_reconciliation_waits_for_the_providers_retry_after(
    api, make_app, mock_provider_url, shared_trip, mock_control, make_worker
):
    """A rate-limited lookup is not retried before Retry-After (45 s here), although the
    backoff alone would retry after 2 s."""
    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    lossy = lost_response_app(make_app, mock_control, mock_provider_url)
    await book(api.as_("ana", app=lossy), shared_trip, item)
    await scenario(mock_control, "supplier-a", "rate_limited", retryAfterSeconds=45)

    worker = make_worker()
    assert await worker.run_once() == 1
    [b] = await bookings(api.as_("ana"), shared_trip)
    assert b["status"] == "unknown"
    assert b["lastError"]["code"] == "PROVIDER_RATE_LIMITED"
    assert b["lastError"]["retryAfterSeconds"] == 45

    await mock_control.delete("/scenario")  # the provider would answer now, but asked for 45 s
    await asyncio.sleep(2.5)  # longer than the first backoff step
    assert await worker.run_once() == 0
    assert (await bookings(api.as_("ana"), shared_trip))[0]["status"] == "unknown"


async def test_a_rate_limited_resend_waits_for_retry_after(
    api, make_app, app, shared_trip, mock_control, provider_client
):
    """The re-send branch (unknown, not found by reference after the grace period) also
    schedules the next attempt after Retry-After."""
    from uuid import UUID

    from app.adapters.clock import SystemClock
    from app.adapters.sqlalchemy.uow import SqlUnitOfWork
    from app.domain.reservations import ReconcilePolicy, sync_booking

    async def lost():
        raise p.ProviderTimeout("lost before reaching the provider")

    item = await add_item(api.as_("ana"), shared_trip, HOTEL)
    lossy = make_app(provider=WrappedProvider(provider_client, before=lost))
    assert (await book(api.as_("ana", app=lossy), shared_trip, item)).json()["status"] == "unknown"

    class RateLimitedResend(WrappedProvider):
        async def create_booking(self, request: Any, *, idempotency_key: str) -> Any:
            raise p.ProviderRateLimited("Too many requests", retry_after_seconds=30)

    [b] = await bookings(api.as_("ana"), shared_trip)
    async with SqlUnitOfWork(app.state.sessions) as uow:
        result = await sync_booking(uow, RateLimitedResend(provider_client), SystemClock(),
                                    UUID(b["bookingId"]), ReconcilePolicy(unknown_grace_seconds=0))
    assert not result.done and result.retry_in == 30
    [after] = await bookings(api.as_("ana"), shared_trip)
    assert after["status"] == "unknown" and after["lastError"]["code"] == "PROVIDER_RATE_LIMITED"
