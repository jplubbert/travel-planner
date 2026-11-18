"""SQLAlchemy implementations of the persistence ports, with explicit row <-> domain mapping."""

from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.sqlalchemy.models import (
    BookingRow,
    DocumentRow,
    ItineraryItemRow,
    JobRow,
    TripChangeRow,
    TripMemberRow,
    TripRow,
    UserRow,
)
from app.domain.access import Role, TripMember
from app.domain.bookings import ACTIVE, Booking, BookingStatus
from app.domain.changes import TripChange
from app.domain.documents import Document, DocumentStatus
from app.domain.errors import Conflict
from app.domain.itinerary import ItineraryItem
from app.domain.jobs import Job, JobStatus
from app.domain.trips import Trip
from app.domain.users import User
from app.observability import request_id_var

# --- Users -------------------------------------------------------------------


class SqlUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> User | None:
        row = await self.session.get(UserRow, user_id)
        return User(row.id, row.display_name) if row else None

    async def list(self) -> list[User]:
        rows = await self.session.scalars(select(UserRow).order_by(UserRow.id))
        return [User(r.id, r.display_name) for r in rows]


# --- Trips and members ---------------------------------------------------------


def _trip(row: TripRow) -> Trip:
    return Trip(
        id=row.id, name=row.name, destinations=tuple(row.destinations),
        starts_on=row.starts_on, ends_on=row.ends_on, travelers=row.travelers,
        budget=row.budget, version=row.version, created_by=row.created_by,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _trip_values(trip: Trip) -> dict[str, object]:
    values = asdict(trip)
    values["destinations"] = list(trip.destinations)
    return values


class SqlTripRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, trip: Trip) -> None:
        self.session.add(TripRow(**_trip_values(trip)))
        await self.session.flush()

    async def get(self, trip_id: UUID) -> Trip | None:
        row = await self.session.scalar(
            select(TripRow).where(TripRow.id == trip_id).execution_options(populate_existing=True)
        )
        return _trip(row) if row else None

    async def get_for_update(self, trip_id: UUID) -> Trip | None:
        row = await self.session.scalar(
            select(TripRow).where(TripRow.id == trip_id).with_for_update()
            .execution_options(populate_existing=True)
        )
        return _trip(row) if row else None

    async def list_for_user(self, user_id: str) -> list[tuple[Trip, Role]]:
        rows = await self.session.execute(
            select(TripRow, TripMemberRow.role)
            .join(TripMemberRow, TripMemberRow.trip_id == TripRow.id)
            .where(TripMemberRow.user_id == user_id)
            .order_by(TripRow.starts_on, TripRow.created_at)
        )
        return [(_trip(trip), Role(role)) for trip, role in rows.all()]

    async def update(self, trip: Trip, *, expected_version: int) -> bool:
        values = _trip_values(trip)
        for key in ("id", "created_by", "created_at"):
            values.pop(key)
        result = await self.session.execute(
            update(TripRow)
            .where(TripRow.id == trip.id, TripRow.version == expected_version)
            .values(**values)
            .returning(TripRow.version)
        )
        return result.scalar_one_or_none() is not None

    async def bump_version(self, trip_id: UUID, *, expected_version: int,
                           now: datetime) -> bool:
        result = await self.session.execute(
            update(TripRow)
            .where(TripRow.id == trip_id, TripRow.version == expected_version)
            .values(version=TripRow.version + 1, updated_at=now)
            .returning(TripRow.version)
        )
        return result.scalar_one_or_none() is not None


class SqlTripChangeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, change: TripChange) -> None:
        self.session.add(TripChangeRow(**asdict(change)))
        await self.session.flush()

    async def list_since(self, trip_id: UUID, after_version: int) -> list[TripChange]:
        rows = await self.session.scalars(
            select(TripChangeRow)
            .where(TripChangeRow.trip_id == trip_id, TripChangeRow.version > after_version)
            .order_by(TripChangeRow.version)
        )
        return [TripChange(r.trip_id, r.version, r.actor_id, r.kind, r.summary, r.at)
                for r in rows]


def _member(row: TripMemberRow) -> TripMember:
    return TripMember(row.trip_id, row.user_id, Role(row.role), row.added_at)


class SqlMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, trip_id: UUID, user_id: str) -> TripMember | None:
        row = await self.session.scalar(
            select(TripMemberRow)
            .where(TripMemberRow.trip_id == trip_id, TripMemberRow.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        return _member(row) if row else None

    async def list(self, trip_id: UUID) -> list[TripMember]:
        rows = await self.session.scalars(
            select(TripMemberRow)
            .where(TripMemberRow.trip_id == trip_id)
            .order_by(TripMemberRow.added_at, TripMemberRow.user_id)
        )
        return [_member(r) for r in rows]

    async def add(self, member: TripMember) -> None:
        self.session.add(TripMemberRow(
            trip_id=member.trip_id, user_id=member.user_id, role=member.role.value,
            added_at=member.added_at,
        ))
        await self.session.flush()

    async def update_role(self, trip_id: UUID, user_id: str, role: Role) -> None:
        await self.session.execute(
            update(TripMemberRow)
            .where(TripMemberRow.trip_id == trip_id, TripMemberRow.user_id == user_id)
            .values(role=role.value)
        )

    async def remove(self, trip_id: UUID, user_id: str) -> None:
        await self.session.execute(
            delete(TripMemberRow)
            .where(TripMemberRow.trip_id == trip_id, TripMemberRow.user_id == user_id)
        )


# --- Itinerary -----------------------------------------------------------------


def _item(row: ItineraryItemRow) -> ItineraryItem:
    snapshot = row.offer_snapshot
    return ItineraryItem(
        id=row.id, trip_id=row.trip_id, position=row.position, offer_id=row.offer_id,
        provider_id=row.provider_id, category=row.category,  # type: ignore[arg-type]
        title=row.title, description=row.description, destination=row.destination,
        origin=row.origin,
        # timestamptz keeps the instant but not the provider's local offset; the snapshot
        # keeps both, and local dates (trip-window checks) depend on the offset.
        starts_at=datetime.fromisoformat(snapshot["startsAt"]),
        ends_at=datetime.fromisoformat(snapshot["endsAt"]),
        travelers=row.travelers, price=row.price, offer_revision=row.offer_revision,
        available=row.available, valid_until=row.valid_until, quoted_at=row.quoted_at,
        added_by=row.added_by, created_at=row.created_at, offer_snapshot=snapshot,
        restrictions=tuple(row.restrictions),
    )


def _item_values(item: ItineraryItem) -> dict[str, object]:
    values = asdict(item)
    values["restrictions"] = list(item.restrictions)
    return values


class SqlItineraryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, item: ItineraryItem) -> None:
        self.session.add(ItineraryItemRow(**_item_values(item)))
        await self.session.flush()

    async def save(self, item: ItineraryItem) -> None:
        values = _item_values(item)
        for key in ("id", "trip_id", "created_at", "added_by"):
            values.pop(key)
        await self.session.execute(
            update(ItineraryItemRow).where(ItineraryItemRow.id == item.id).values(**values))

    async def remove(self, item_id: UUID) -> None:
        await self.session.execute(delete(ItineraryItemRow).where(ItineraryItemRow.id == item_id))

    async def set_positions(self, positions: dict[UUID, int]) -> None:
        for item_id, position in positions.items():
            await self.session.execute(
                update(ItineraryItemRow).where(ItineraryItemRow.id == item_id)
                .values(position=position))

    async def get(self, item_id: UUID) -> ItineraryItem | None:
        row = await self.session.scalar(
            select(ItineraryItemRow).where(ItineraryItemRow.id == item_id)
            .execution_options(populate_existing=True))
        return _item(row) if row else None

    async def list_for_trip(self, trip_id: UUID) -> list[ItineraryItem]:
        rows = await self.session.scalars(
            select(ItineraryItemRow)
            .where(ItineraryItemRow.trip_id == trip_id)
            .order_by(ItineraryItemRow.position, ItineraryItemRow.created_at)
            .execution_options(populate_existing=True)
        )
        return [_item(r) for r in rows]


# --- Bookings ------------------------------------------------------------------


def _booking(row: BookingRow) -> Booking:
    return Booking(
        id=row.id, trip_id=row.trip_id, itinerary_item_id=row.itinerary_item_id,
        offer_id=row.offer_id, expected_revision=row.expected_revision,
        expected_price=row.expected_price, client_reference=row.client_reference,
        idempotency_key=row.idempotency_key, requested_by=row.requested_by,
        status=BookingStatus(row.status), created_at=row.created_at, updated_at=row.updated_at,
        submitted_at=row.submitted_at, provider_booking_id=row.provider_booking_id,
        accepted_offer=row.accepted_offer, accepted_price=row.accepted_price,
        failure_code=row.failure_code, failure_message=row.failure_message,
        last_error=row.last_error, rejection_terms=row.rejection_terms, version=row.version,
    )


def _booking_values(booking: Booking) -> dict[str, object]:
    values = asdict(booking)
    values["status"] = booking.status.value
    return values


class SqlBookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, booking: Booking) -> None:
        try:
            async with self.session.begin_nested():
                self.session.add(BookingRow(**_booking_values(booking)))
                await self.session.flush()
        except IntegrityError as exc:
            if "uq_bookings_one_active_per_item" in str(exc.orig):
                raise Conflict("BOOKING_IN_PROGRESS",
                               "This option already has an active booking attempt.") from None
            raise

    async def get(self, booking_id: UUID) -> Booking | None:
        row = await self.session.get(BookingRow, booking_id, populate_existing=True)
        return _booking(row) if row else None

    async def get_active_for_item(self, item_id: UUID) -> Booking | None:
        row = await self.session.scalar(
            select(BookingRow).where(
                BookingRow.itinerary_item_id == item_id,
                BookingRow.status.in_([s.value for s in ACTIVE]))
            .execution_options(populate_existing=True))
        return _booking(row) if row else None

    async def list_stale_submitted(self, before: datetime) -> list[Booking]:
        rows = await self.session.scalars(
            select(BookingRow).where(BookingRow.status == BookingStatus.SUBMITTED.value,
                                     BookingRow.submitted_at < before)
            .order_by(BookingRow.submitted_at))
        return [_booking(r) for r in rows]

    async def list_for_trip(self, trip_id: UUID) -> list[Booking]:
        rows = await self.session.scalars(
            select(BookingRow).where(BookingRow.trip_id == trip_id)
            .order_by(BookingRow.created_at)
            .execution_options(populate_existing=True)
        )
        return [_booking(r) for r in rows]

    async def save(self, booking: Booking) -> bool:
        values = _booking_values(booking)
        read_version = values.pop("version")
        for key in ("id", "trip_id", "created_at", "client_reference", "idempotency_key"):
            values.pop(key)
        result = await self.session.execute(
            update(BookingRow)
            .where(BookingRow.id == booking.id, BookingRow.version == read_version)
            .values(**values, version=BookingRow.version + 1)
            .returning(BookingRow.version)
        )
        new_version = result.scalar_one_or_none()
        if new_version is None:
            return False
        booking.version = new_version
        return True


# --- Documents and jobs ------------------------------------------------------------


def _document(row: DocumentRow) -> Document:
    return Document(
        id=row.id, trip_id=row.trip_id, trip_version=row.trip_version,
        requested_by=row.requested_by, status=DocumentStatus(row.status),
        created_at=row.created_at, updated_at=row.updated_at, snapshot=row.snapshot,
        snapshot_at=row.snapshot_at, snapshot_sha256=row.snapshot_sha256,
        storage_key=row.storage_key, content_sha256=row.content_sha256,
        size_bytes=row.size_bytes, error=row.error, completed_at=row.completed_at,
    )


class SqlDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, document: Document) -> None:
        values = asdict(document)
        values["status"] = document.status.value
        try:
            async with self.session.begin_nested():
                self.session.add(DocumentRow(**values))
                await self.session.flush()
        except IntegrityError as exc:
            if "uq_documents_one_active_per_version" in str(exc.orig):
                raise Conflict("DOCUMENT_IN_PROGRESS",
                               "A document for this trip version is already being "
                               "generated.") from None
            raise

    async def get(self, document_id: UUID) -> Document | None:
        row = await self.session.get(DocumentRow, document_id, populate_existing=True)
        return _document(row) if row else None

    async def list_for_trip(self, trip_id: UUID) -> list[Document]:
        rows = await self.session.scalars(
            select(DocumentRow).where(DocumentRow.trip_id == trip_id)
            .order_by(DocumentRow.created_at)
            .execution_options(populate_existing=True)
        )
        return [_document(r) for r in rows]

    async def list_all(self) -> list[Document]:
        rows = await self.session.scalars(
            select(DocumentRow).order_by(DocumentRow.created_at)
            .execution_options(populate_existing=True))
        return [_document(r) for r in rows]

    async def save(self, document: Document, *, expected_status: str) -> bool:
        values = asdict(document)
        values["status"] = document.status.value
        for key in ("id", "trip_id", "trip_version", "requested_by", "created_at",
                    "snapshot", "snapshot_at", "snapshot_sha256"):
            values.pop(key)  # never rewritten (and guarded by a trigger)
        result = await self.session.execute(
            update(DocumentRow)
            .where(DocumentRow.id == document.id, DocumentRow.status == str(expected_status))
            .values(**values)
            .returning(DocumentRow.id)
        )
        return result.scalar_one_or_none() is not None


def _job(row: JobRow) -> Job:
    return Job(
        id=row.id, kind=row.kind, status=JobStatus(row.status), run_after=row.run_after,
        created_at=row.created_at, updated_at=row.updated_at, payload=row.payload,
        dedupe_key=row.dedupe_key, attempts=row.attempts, max_attempts=row.max_attempts,
        locked_until=row.locked_until, last_error=row.last_error, request_id=row.request_id,
    )


def _job_values(job: Job) -> dict[str, Any]:
    values = asdict(job)
    values["status"] = job.status.value
    if values["request_id"] is None:  # created while serving a request: keep its id
        values["request_id"] = request_id_var.get()
    return values


class SqlJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, job: Job) -> None:
        self.session.add(JobRow(**_job_values(job)))
        await self.session.flush()

    async def get(self, job_id: UUID) -> Job | None:
        row = await self.session.get(JobRow, job_id, populate_existing=True)
        return _job(row) if row else None

    async def enqueue(self, job: Job) -> bool:
        result = await self.session.execute(
            insert(JobRow).values(**_job_values(job))
            .on_conflict_do_nothing(
                index_elements=["dedupe_key"],
                # Literal predicate: with bound parameters Postgres cannot match the
                # partial index once asyncpg switches to a generic prepared plan.
                index_where=text("status IN ('queued', 'running')"))
            .returning(JobRow.id))
        return result.scalar_one_or_none() is not None

    async def claim(self, now: datetime, lease_until: datetime, limit: int) -> list[Job]:
        due = await self.session.scalars(
            select(JobRow)
            .where(or_(
                (JobRow.status == "queued") & (JobRow.run_after <= now),
                (JobRow.status == "running") & (JobRow.locked_until < now),  # lease expired
            ))
            .order_by(JobRow.run_after)
            .limit(limit)
            .with_for_update(skip_locked=True)  # concurrent workers skip rows taken here
            .execution_options(populate_existing=True))
        claimed = []
        for row in due:
            row.status = "running"
            row.locked_until = lease_until
            row.attempts += 1
            row.updated_at = now
            claimed.append(_job(row))
        await self.session.flush()
        return claimed

    async def save(self, job: Job) -> None:
        values = asdict(job)
        values["status"] = job.status.value
        for key in ("id", "kind", "created_at"):
            values.pop(key)
        await self.session.execute(update(JobRow).where(JobRow.id == job.id).values(**values))
