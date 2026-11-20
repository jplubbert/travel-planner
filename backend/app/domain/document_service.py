"""US-09 (generate a trip document) and US-10 (access the right document).

Requesting a document takes an immutable snapshot (trip, itinerary with its checks, costs
and booking statuses, with the trip version and time) in the same transaction that
records the document and enqueues its job. Generation renders that snapshot, so later
edits never change an existing document, and a lost or damaged file can be regenerated
from the same snapshot.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from app.domain.access import Role, TripAccess
from app.domain.bookings import Booking, next_step
from app.domain.costs import ROUNDING, summarize
from app.domain.documents import (
    ACTIVE,
    Document,
    DocumentStatus,
    canonical_sha256,
    content_sha256,
)
from app.domain.errors import Conflict, NotFound, trip_not_found
from app.domain.itinerary import build_view
from app.domain.jobs import Job, JobStatus
from app.domain.money import format_usd
from app.domain.trips import Trip

if TYPE_CHECKING:
    from app.ports.clock import Clock
    from app.ports.documents import DocumentRenderer, DocumentStorage
    from app.ports.repositories import UnitOfWork

D = DocumentStatus
GENERATE_JOB = "generate_document"
SNAPSHOT_SCHEMA = 1


def _money(amount: Decimal | None) -> dict[str, str] | None:
    return None if amount is None else {"amount": format_usd(amount), "currency": "USD"}


def take_snapshot(trip: Trip, items: list[Any], bookings: list[Booking],
                  now: datetime) -> dict[str, Any]:
    """Everything the document shows, as plain JSON, computed once."""
    view = build_view(trip, items, bookings, now)
    costs = summarize(items, bookings, trip.budget)
    titles = {i.id: i.title for i in items}
    return {
        "schema": SNAPSHOT_SCHEMA,
        "takenAt": now.isoformat(),
        "trip": {
            "id": str(trip.id), "version": trip.version, "name": trip.name,
            "destinations": list(trip.destinations), "startsOn": trip.starts_on.isoformat(),
            "endsOn": trip.ends_on.isoformat(), "travelers": trip.travelers,
            "budget": _money(trip.budget), "preferences": trip.preferences,
        },
        "itinerary": [{
            "title": v.item.title, "category": v.item.category,
            "destination": v.item.destination, "origin": v.item.origin,
            "startsAt": v.item.starts_at.isoformat(), "endsAt": v.item.ends_at.isoformat(),
            "providerId": v.item.provider_id, "offerId": v.item.offer_id,
            "quotedPrice": _money(v.item.price), "quoteValidUntil":
                v.item.valid_until.isoformat(), "restrictions": list(v.item.restrictions),
            "flags": [{"code": f.code, "message": f.message} for f in v.flags],
        } for v in view.items],
        "missing": [{"code": g.code, "message": g.message} for g in view.missing],
        "rules": list(view.rules),
        "costs": {
            "lines": [{"title": line.title, "category": line.category,
                       "amount": _money(line.amount), "basis": line.basis,
                       "bookingStatus": line.booking_status.value if line.booking_status
                       else None, "note": line.note} for line in costs.lines],
            "estimatedTotal": _money(costs.estimated_total),
            "confirmedTotal": _money(costs.confirmed_total),
            "total": _money(costs.total),
            "budget": _money(costs.budget), "remaining": _money(costs.remaining),
            "overBudget": costs.over_budget,
            "usedPercent": str(costs.used_percent) if costs.used_percent is not None
            else None,
            "rounding": ROUNDING,
        },
        "bookings": [{
            "item": titles.get(b.itinerary_item_id) if b.itinerary_item_id else None,
            "offerId": b.offer_id, "status": b.status.value,
            "booked": b.status.value == "confirmed",
            "acceptedPrice": _money(b.accepted_price),
            "providerBookingId": b.provider_booking_id,
            "failure": {"code": b.failure_code, "message": b.failure_message}
            if b.failure_code else None,
            "nextStep": next_step(b), "updatedAt": b.updated_at.isoformat(),
        } for b in sorted(bookings, key=lambda b: b.created_at)],
    }


def snapshot_identity(snapshot: dict[str, Any]) -> str:
    """Hash of what the document represents; the time it was taken is not part of it."""
    return canonical_sha256({k: v for k, v in snapshot.items() if k != "takenAt"})


# --- Requests and reads --------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentRequestResult:
    document: Document
    created: bool  # False: an equivalent document already existed and is returned


async def request_document(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, max_attempts: int = 3
) -> DocumentRequestResult:
    access.require(Role.EDITOR)
    # Lock the trip row so the snapshot and the version describe the same state.
    trip = await uow.trips.get_for_update(access.trip_id)
    if trip is None:
        raise trip_not_found()
    now = clock.now()
    items = await uow.itinerary.list_for_trip(trip.id)
    bookings = await uow.bookings.list_for_trip(trip.id)
    snapshot = take_snapshot(trip, items, bookings, now)
    identity = snapshot_identity(snapshot)

    for existing in await uow.documents.list_for_trip(trip.id):
        same_version_running = existing.trip_version == trip.version and \
            existing.status in ACTIVE
        same_content_done = existing.snapshot_sha256 == identity and \
            existing.status == D.COMPLETE
        if same_version_running or same_content_done:
            return DocumentRequestResult(existing, created=False)

    document = Document(
        id=uuid4(), trip_id=trip.id, trip_version=trip.version, requested_by=access.user_id,
        status=D.WAITING, created_at=now, updated_at=now, snapshot=snapshot, snapshot_at=now,
        snapshot_sha256=identity,
    )
    try:
        await uow.documents.add(document)
    except Conflict:  # a concurrent request for this version won; return that one
        await uow.rollback()
        running = [d for d in await uow.documents.list_for_trip(trip.id)
                   if d.trip_version == trip.version and d.status in ACTIVE]
        if running:
            return DocumentRequestResult(running[0], created=False)
        raise
    await enqueue_generation(uow, clock, document.id, max_attempts)
    await uow.commit()  # document, snapshot and job: all or nothing
    return DocumentRequestResult(document, created=True)


async def enqueue_generation(uow: "UnitOfWork", clock: "Clock", document_id: UUID,
                             max_attempts: int = 3) -> None:
    now = clock.now()
    await uow.jobs.enqueue(Job(
        id=uuid4(), kind=GENERATE_JOB, status=JobStatus.QUEUED, run_after=now,
        created_at=now, updated_at=now, payload={"documentId": str(document_id)},
        dedupe_key=f"{GENERATE_JOB}:{document_id}", max_attempts=max_attempts,
    ))


async def list_documents(uow: "UnitOfWork", access: TripAccess) -> list[Document]:
    access.require(Role.VIEWER)
    return await uow.documents.list_for_trip(access.trip_id)


async def get_document(uow: "UnitOfWork", access: TripAccess, raw_id: str) -> Document:
    access.require(Role.VIEWER)
    try:
        document = await uow.documents.get(UUID(raw_id))
    except ValueError:
        document = None
    if document is None or document.trip_id != access.trip_id:
        raise NotFound("DOCUMENT_NOT_FOUND", "Document not found.")
    return document


async def retry_document(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, raw_id: str, max_attempts: int = 3
) -> Document:
    access.require(Role.EDITOR)
    document = await get_document(uow, access, raw_id)
    if document.status != D.FAILED:
        raise Conflict("DOCUMENT_NOT_RETRYABLE",
                       f"Only a failed document can be retried; this one is {document.status}.")
    document.move(D.WAITING, clock.now())
    document.error = None
    if not await uow.documents.save(document, expected_status=D.FAILED):
        raise Conflict("DOCUMENT_CHANGED", "The document changed meanwhile; reload it.")
    await enqueue_generation(uow, clock, document.id, max_attempts)
    await uow.commit()
    return document


@dataclass(frozen=True)
class DownloadResult:
    document: Document
    content: bytes


async def download_document(
    uow: "UnitOfWork", storage: "DocumentStorage", clock: "Clock", access: TripAccess,
    raw_id: str, max_attempts: int = 3,
) -> DownloadResult:
    """Delivers the file only if it is exactly what was recorded as complete. A missing or
    altered file is never served: the problem is recorded and the document is regenerated
    from its snapshot."""
    document = await get_document(uow, access, raw_id)
    if document.status != D.COMPLETE:
        raise Conflict("DOCUMENT_NOT_READY",
                       f"The document is {document.status.replace('_', ' ')}, not ready to "
                       "download.", {"status": document.status.value})
    assert document.storage_key is not None
    content = await storage.read(document.storage_key)
    problem = None
    if content is None:
        problem = "FILE_MISSING"
    elif content_sha256(content) != document.content_sha256 or \
            len(content) != document.size_bytes:
        problem = "FILE_CORRUPT"
    if problem is None:
        assert content is not None
        return DownloadResult(document, content)

    now = clock.now()
    document.move(D.WAITING, now)
    document.error = (f"{problem} detected at {now.isoformat()}; regenerating from the "
                      "saved snapshot.")
    if await uow.documents.save(document, expected_status=D.COMPLETE):
        await enqueue_generation(uow, clock, document.id, max_attempts)
        await uow.commit()
    raise Conflict("DOCUMENT_FILE_UNAVAILABLE",
                   "The stored file is missing or does not match its recorded checksum, so "
                   "it was not delivered. It is being regenerated from the saved snapshot; "
                   "try again shortly.", {"problem": problem, "status": D.WAITING.value})


# --- Generation (run by the worker) ----------------------------------------------------------


@dataclass(frozen=True)
class GenerationResult:
    done: bool


async def generate_document(
    uow: "UnitOfWork", storage: "DocumentStorage", renderer: "DocumentRenderer",
    clock: "Clock", document_id: UUID,
) -> GenerationResult:
    """Renders the snapshot, stores the file atomically, and only then marks the document
    complete with its checksum and size. Safe to re-run after any interruption."""
    document = await uow.documents.get(document_id)
    if document is None or document.status in (D.COMPLETE, D.FAILED):
        return GenerationResult(done=True)
    if document.status == D.WAITING:
        document.move(D.IN_PROGRESS, clock.now())
        if not await uow.documents.save(document, expected_status=D.WAITING):
            return GenerationResult(done=False)
        await uow.commit()
    await uow.rollback()

    content = renderer.render(document.snapshot)
    await storage.write(document.file_key, content)  # temp file, fsync, atomic rename

    document.move(D.COMPLETE, clock.now())
    document.storage_key = document.file_key
    document.content_sha256 = content_sha256(content)
    document.size_bytes = len(content)
    document.completed_at = clock.now()
    document.error = None
    if not await uow.documents.save(document, expected_status=D.IN_PROGRESS):
        return GenerationResult(done=False)
    await uow.commit()
    return GenerationResult(done=True)


async def give_up_generation(uow: "UnitOfWork", clock: "Clock", document_id: UUID,
                             reason: str) -> None:
    document = await uow.documents.get(document_id)
    if document is None or document.status not in ACTIVE:
        return
    previous = document.status
    document.move(D.FAILED, clock.now())
    document.error = reason
    if await uow.documents.save(document, expected_status=previous):
        await uow.commit()


# --- Storage consistency ---------------------------------------------------------------------


@dataclass(frozen=True)
class SweepReport:
    orphans_removed: list[str]
    temp_files_removed: int
    missing_files: list[UUID]


async def sweep_documents(
    uow: "UnitOfWork", storage: "DocumentStorage", clock: "Clock",
    temp_older_than_seconds: float = 600.0, max_attempts: int = 3,
) -> SweepReport:
    """Reconciles files with document records: removes files no document refers to (and
    stale temporary files from interrupted writes), and sends complete documents whose file
    disappeared back to generation."""
    documents = await uow.documents.list_all()
    expected = {d.file_key for d in documents}
    orphans = [key for key in await storage.list_keys() if key not in expected]
    for key in orphans:
        await storage.delete(key)
    temp_removed = await storage.remove_temp_files(
        clock.now() - timedelta(seconds=temp_older_than_seconds))

    missing = []
    for d in documents:
        if d.status == D.COMPLETE and d.storage_key and not await storage.exists(d.storage_key):
            d.move(D.WAITING, clock.now())
            d.error = f"FILE_MISSING detected by storage sweep at {clock.now().isoformat()}"
            if await uow.documents.save(d, expected_status=D.COMPLETE):
                await enqueue_generation(uow, clock, d.id, max_attempts)
                missing.append(d.id)
    await uow.commit()
    return SweepReport(orphans, temp_removed, missing)
