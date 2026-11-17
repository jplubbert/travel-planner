"""US-07 (confirm current terms before booking) and US-08 (uncertain and partial bookings).

Implements ADR 0003. The sequence for one attempt is:

1. Re-read the offer from the provider and compare it with the terms the user reviewed.
   If anything differs, nothing is sent and the user gets the current terms (409).
2. Persist the attempt as ``submitted`` with its clientReference and Idempotency-Key in its
   own transaction, *before* calling the provider (at most one active attempt per item,
   enforced by the database).
3. Send ``POST /bookings`` and record what the answer proves; enqueue reconciliation
   whenever the outcome is not final.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from app.domain.access import Role, TripAccess
from app.domain.bookings import Booking, BookingStatus
from app.domain.errors import Conflict, FieldError, NotFound, ValidationFailed
from app.domain.itinerary import ItineraryItem, offer_snapshot
from app.domain.jobs import Job, JobStatus
from app.domain.money import parse_usd
from app.domain.provider import (
    BookingRequest,
    Offer,
    OfferChanged,
    OfferUnavailable,
    ProviderBadRequest,
    ProviderBooking,
    ProviderError,
    ProviderNotFound,
    ProviderRateLimited,
    ReferenceConflict,
)
from app.domain.trips import get_trip

if TYPE_CHECKING:
    from app.ports.clock import Clock
    from app.ports.provider import ProviderClient
    from app.ports.repositories import UnitOfWork

S = BookingStatus
SYNC_JOB = "sync_booking"


@dataclass(frozen=True)
class ReconcilePolicy:
    # How long an unknown booking keeps getting 404 by reference before the original
    # request is re-sent with the same Idempotency-Key (ADR 0003 rule 4.2).
    unknown_grace_seconds: float = 30.0
    # A booking still ``submitted`` after this long lost its sender (ADR 0003 rule 3).
    # Must exceed the provider timeout.
    submitted_stale_seconds: float = 60.0
    pending_poll_seconds: float = 5.0
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 60.0


def error_info(exc: ProviderError, now: datetime) -> dict[str, Any]:
    return {
        "code": exc.code,
        "message": exc.message,
        "providerRequestId": exc.provider_request_id,
        "retryAfterSeconds": exc.retry_after_seconds
        if isinstance(exc, ProviderRateLimited) else None,
        "at": now.isoformat(),
    }


def _terms(offer: Offer) -> dict[str, Any]:
    return offer_snapshot(offer)


# --- Reads --------------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingView:
    booking: Booking
    item_title: str | None


async def list_bookings(uow: "UnitOfWork", access: TripAccess) -> list[BookingView]:
    access.require(Role.VIEWER)
    trip = await get_trip(uow, access)
    titles = {i.id: i.title for i in await uow.itinerary.list_for_trip(trip.id)}
    return [BookingView(b, titles.get(b.itinerary_item_id) if b.itinerary_item_id else
                        (b.accepted_offer or {}).get("title"))
            for b in await uow.bookings.list_for_trip(trip.id)]


async def get_booking(uow: "UnitOfWork", access: TripAccess, raw_id: str) -> BookingView:
    access.require(Role.VIEWER)
    booking = await _booking_of_trip(uow, access.trip_id, raw_id)
    item = (await uow.itinerary.get(booking.itinerary_item_id)
            if booking.itinerary_item_id else None)
    return BookingView(booking, item.title if item else None)


async def _booking_of_trip(uow: "UnitOfWork", trip_id: UUID, raw_id: str) -> Booking:
    try:
        booking = await uow.bookings.get(UUID(raw_id))
    except ValueError:
        booking = None
    if booking is None or booking.trip_id != trip_id:
        raise NotFound("BOOKING_NOT_FOUND", "Booking not found.")
    return booking


# --- US-07: request a booking ------------------------------------------------------------


async def request_booking(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", access: TripAccess,
    raw_item_id: str, expected_revision: object, expected_price: object,
) -> BookingView:
    """Explicit editor action on one itinerary item, with the terms the user reviewed."""
    access.require(Role.EDITOR)
    trip = await get_trip(uow, access)
    item = await _item_of_trip(uow, trip.id, raw_item_id)
    revision, price = _reviewed_terms(expected_revision, expected_price)
    await _ensure_no_active_attempt(uow, item)
    await uow.rollback()  # no database connection held during the provider call

    try:
        offer = await provider.get_offer(item.offer_id)
    except ProviderNotFound:
        raise Conflict("OFFER_NO_LONGER_OFFERED",
                       f"The provider no longer offers '{item.title}'. Nothing was booked.") \
            from None
    if not offer.available:
        raise Conflict("OFFER_UNAVAILABLE",
                       "The provider reports this option as unavailable. Nothing was booked.",
                       {"currentTerms": _terms(offer)})
    if offer.travelers != trip.travelers:
        raise Conflict("PARTY_SIZE_MISMATCH",
                       f"This option is quoted for {offer.travelers} travelers but the trip "
                       f"has {trip.travelers}. Nothing was booked.")
    if offer.revision != revision or offer.price != price:
        raise Conflict("TERMS_CHANGED",
                       "The price or terms changed since you reviewed them. Nothing was "
                       "booked. Review the current terms and confirm again.",
                       {"currentTerms": _terms(offer), "reviewed": {
                           "revision": revision, "price": f"{price:.2f}"}})

    now = clock.now()
    booking_id = uuid4()
    booking = Booking(
        id=booking_id, trip_id=trip.id, itinerary_item_id=item.id, offer_id=offer.id,
        expected_revision=offer.revision, expected_price=offer.price,
        client_reference=f"tp-{booking_id}", idempotency_key=f"tp-key-{uuid4()}",
        requested_by=access.user_id, status=S.DRAFT, created_at=now, updated_at=now,
    )
    booking.submit(now)
    await uow.bookings.add(booking)  # raises Conflict on a concurrent second attempt
    await uow.commit()  # durable before anything reaches the provider (ADR 0003 rule 2)

    await send(uow, provider, clock, booking)
    return BookingView(booking, item.title)


async def retry_booking(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", access: TripAccess,
    raw_booking_id: str,
) -> BookingView:
    """Re-sends a draft (not accepted, e.g. after 429/503) with the same key and body."""
    access.require(Role.EDITOR)
    booking = await _booking_of_trip(uow, access.trip_id, raw_booking_id)
    if booking.status != S.DRAFT:
        raise Conflict("BOOKING_NOT_RETRYABLE",
                       f"Only a booking that was not accepted can be retried; this one is "
                       f"{booking.status}.")
    booking.submit(clock.now())
    if not await uow.bookings.save(booking):
        raise Conflict("BOOKING_IN_PROGRESS",
                       "Someone else is already retrying this booking; reload to see it.")
    await uow.commit()
    await send(uow, provider, clock, booking)
    item = (await uow.itinerary.get(booking.itinerary_item_id)
            if booking.itinerary_item_id else None)
    return BookingView(booking, item.title if item else None)


def _reviewed_terms(expected_revision: object, expected_price: object) -> tuple[str, Decimal]:
    errors: list[FieldError] = []
    if not isinstance(expected_revision, str) or not expected_revision:
        errors.append(FieldError("expectedRevision", "is required: the revision you reviewed"))
    price = None
    if not isinstance(expected_price, dict) or expected_price.get("currency") != "USD":
        errors.append(FieldError("expectedPrice", "is required as {amount, currency: 'USD'}"))
    else:
        try:
            price = parse_usd(expected_price.get("amount"))
        except ValueError as exc:
            errors.append(FieldError("expectedPrice.amount", str(exc)))
    if errors:
        raise ValidationFailed(errors)
    assert isinstance(expected_revision, str) and price is not None
    return expected_revision, price


async def _item_of_trip(uow: "UnitOfWork", trip_id: UUID, raw_id: str) -> ItineraryItem:
    try:
        item = await uow.itinerary.get(UUID(raw_id))
    except ValueError:
        item = None
    if item is None or item.trip_id != trip_id:
        raise NotFound("ITEM_NOT_FOUND", "Itinerary item not found.")
    return item


async def _ensure_no_active_attempt(uow: "UnitOfWork", item: ItineraryItem) -> None:
    active = await uow.bookings.get_active_for_item(item.id)
    if active is None:
        return
    if active.status == S.CONFIRMED:
        raise Conflict("ITEM_ALREADY_BOOKED", f"'{item.title}' is already booked.",
                       {"bookingId": str(active.id)})
    raise Conflict("BOOKING_IN_PROGRESS",
                   f"'{item.title}' already has a {active.status} booking attempt. "
                   + ("Retry it instead of starting a new one." if active.status == S.DRAFT
                      else "Wait for its outcome."),
                   {"bookingId": str(active.id), "status": active.status.value})


# --- Sending and recording outcomes ----------------------------------------------------------


def _request(booking: Booking) -> BookingRequest:
    return BookingRequest(booking.client_reference, booking.offer_id, booking.expected_revision,
                          booking.expected_price)


async def send(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", booking: Booking
) -> None:
    """POST /bookings for a ``submitted`` (or resent ``unknown``) booking and record what the
    answer proves. Any non-provider exception propagates and leaves the booking as it was in
    the database (``submitted``), which the worker later turns into ``unknown``."""
    try:
        result = await provider.create_booking(_request(booking),
                                               idempotency_key=booking.idempotency_key)
    except ProviderError as exc:
        await _record_failure(uow, provider, clock, booking, exc)
    else:
        _adopt(booking, result, clock.now())
    await _save_and_schedule(uow, clock, booking, ReconcilePolicy())


async def _record_failure(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", booking: Booking,
    exc: ProviderError,
) -> None:
    now = clock.now()
    if isinstance(exc, (OfferChanged, OfferUnavailable, ProviderBadRequest, ProviderNotFound)):
        terms = None
        if isinstance(exc, (OfferChanged, OfferUnavailable)):
            try:  # best effort: show the user what the terms are now
                terms = _terms(await provider.get_offer(booking.offer_id))
            except ProviderError:
                terms = None
        booking.record_rejection(exc.code, exc.message, now, terms)
        booking.last_error = error_info(exc, now)
    elif isinstance(exc, ReferenceConflict) or exc.delivery_uncertain:
        # Either the answer was lost, or a booking with this reference exists: the outcome
        # can only be learned by reconciling.
        if booking.status == S.SUBMITTED:
            booking.record_unknown(error_info(exc, now), now)
        else:
            booking.note_error(error_info(exc, now), now)
    elif booking.status == S.SUBMITTED:
        booking.record_not_accepted(error_info(exc, now), now)
    else:  # a resend from ``unknown`` that was not accepted: still unknown
        booking.note_error(error_info(exc, now), now)


def _adopt(booking: Booking, result: ProviderBooking, now: datetime) -> None:
    if booking.status == S.PENDING and result.status == "pending":
        return
    booking.record_provider_booking(
        provider_booking_id=result.id, provider_status=result.status,
        offer=offer_snapshot(result.offer), price=result.offer.price, failure=result.failure,
        now=now,
    )


async def _save_and_schedule(
    uow: "UnitOfWork", clock: "Clock", booking: Booking, policy: ReconcilePolicy,
    delay: float = 0.0,
) -> bool:
    """Saves the booking (optimistic) and, if its outcome is still open, enqueues the sync
    job in the same transaction so it can never be forgotten."""
    if not await uow.bookings.save(booking):
        await uow.rollback()
        fresh = await uow.bookings.get(booking.id)
        if fresh is not None:  # someone else (worker or retry) moved it; theirs wins
            booking.__dict__.update(fresh.__dict__)
        return False
    if booking.status in (S.UNKNOWN, S.PENDING):
        await enqueue_sync(uow, clock, booking.id, delay)
    await uow.commit()
    return True


async def enqueue_sync(uow: "UnitOfWork", clock: "Clock", booking_id: UUID,
                       delay: float = 0.0) -> None:
    now = clock.now()
    await uow.jobs.enqueue(Job(
        id=uuid4(), kind=SYNC_JOB, status=JobStatus.QUEUED,
        run_after=now + timedelta(seconds=delay), created_at=now, updated_at=now,
        payload={"bookingId": str(booking_id)}, dedupe_key=f"{SYNC_JOB}:{booking_id}",
    ))


# --- US-08: reconciliation (run by the worker) -----------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    done: bool  # the job can finish
    retry_in: float = 0.0  # otherwise, check again after this many seconds


def backoff(attempts: int, policy: ReconcilePolicy) -> float:
    return float(min(policy.retry_max_seconds,
                     policy.retry_base_seconds * 2 ** max(0, attempts - 1)))


async def sync_booking(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", booking_id: UUID,
    policy: ReconcilePolicy, attempts: int = 1,
) -> SyncResult:
    """One reconciliation step for a booking (ADR 0003 rules 3-5). Idempotent: it re-reads
    the booking and decides from its current state every time."""
    booking = await uow.bookings.get(booking_id)
    if booking is None or booking.status.terminal or booking.status == S.DRAFT:
        return SyncResult(done=True)
    now = clock.now()

    if booking.status == S.SUBMITTED:
        assert booking.submitted_at is not None
        if now - booking.submitted_at < timedelta(seconds=policy.submitted_stale_seconds):
            return SyncResult(False, policy.submitted_stale_seconds)  # sender may be alive
        booking.record_unknown({"code": "INTERRUPTED", "message": "The request was sent or "
                                "about to be sent when the sender stopped; outcome unknown.",
                                "providerRequestId": None, "retryAfterSeconds": None,
                                "at": now.isoformat()}, now)
        if not await uow.bookings.save(booking):
            return SyncResult(False, 0)
        await uow.commit()
    await uow.rollback()  # do not hold a connection during provider calls

    if booking.status == S.PENDING:
        assert booking.provider_booking_id is not None
        try:
            result = await provider.get_booking(booking.provider_booking_id)
        except ProviderError as exc:
            booking.note_error(error_info(exc, now), now)  # still pending: it was accepted
            await _save_and_schedule(uow, clock, booking, policy, backoff(attempts, policy))
            return SyncResult(False, backoff(attempts, policy))
        _adopt(booking, result, clock.now())
        await _save_and_schedule(uow, clock, booking, policy, policy.pending_poll_seconds)
        return _after(booking, policy.pending_poll_seconds)

    # unknown: look it up by reference first
    try:
        found = await provider.get_booking_by_reference(booking.client_reference)
    except ProviderNotFound:
        found = None
    except ProviderError as exc:
        booking.note_error(error_info(exc, now), now)
        await _save_and_schedule(uow, clock, booking, policy, backoff(attempts, policy))
        return SyncResult(False, backoff(attempts, policy))
    if found is not None:
        _adopt(booking, found, clock.now())
        await _save_and_schedule(uow, clock, booking, policy, policy.pending_poll_seconds)
        return _after(booking, policy.pending_poll_seconds)

    assert booking.submitted_at is not None
    waited = (now - booking.submitted_at).total_seconds()
    if waited < policy.unknown_grace_seconds:
        return SyncResult(False, policy.unknown_grace_seconds - waited)
    # Still not visible after the grace period: re-send the same request with the same key.
    await send(uow, provider, clock, booking)
    return _after(booking, backoff(attempts, policy))


def _after(booking: Booking, retry_in: float) -> SyncResult:
    if booking.status.terminal:
        return SyncResult(done=True)
    return SyncResult(False, retry_in)


async def sweep_stale_submitted(
    uow: "UnitOfWork", clock: "Clock", policy: ReconcilePolicy
) -> int:
    """Makes sure every ``submitted`` booking whose sender may have died gets reconciled
    (run at worker start-up and periodically)."""
    cutoff = clock.now() - timedelta(seconds=policy.submitted_stale_seconds)
    stale = await uow.bookings.list_stale_submitted(cutoff)
    for booking in stale:
        await enqueue_sync(uow, clock, booking.id)
    await uow.commit()
    return len(stale)
