"""Persistence ports. The domain depends on these protocols, never on SQLAlchemy."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.access import Role, TripMember
from app.domain.bookings import Booking
from app.domain.changes import TripChange
from app.domain.documents import Document
from app.domain.itinerary import ItineraryItem
from app.domain.jobs import Job
from app.domain.trips import Trip
from app.domain.users import User


class UserRepository(Protocol):
    async def get(self, user_id: str) -> User | None: ...
    async def list(self) -> list[User]: ...


class TripRepository(Protocol):
    async def add(self, trip: Trip) -> None: ...
    async def get(self, trip_id: UUID) -> Trip | None: ...

    async def get_for_update(self, trip_id: UUID) -> Trip | None:
        """Reads the trip and locks its row until the transaction ends, so every later read
        in the transaction describes this same version."""
        ...

    async def list_for_user(self, user_id: str) -> list[tuple[Trip, Role]]: ...

    async def update(self, trip: Trip, *, expected_version: int) -> bool:
        """Writes ``trip`` only if the stored version is still ``expected_version``
        (atomic compare-and-set). Returns False on a version conflict."""
        ...

    async def bump_version(self, trip_id: UUID, *, expected_version: int,
                           now: datetime) -> bool:
        """Compare-and-set ``version`` to ``expected_version + 1``; locks the trip row until
        the transaction ends. Returns False if the version is no longer expected."""
        ...


class TripChangeRepository(Protocol):
    async def add(self, change: TripChange) -> None: ...

    async def list_since(self, trip_id: UUID, after_version: int) -> list[TripChange]:
        """Changes that produced versions greater than ``after_version``, oldest first."""
        ...


class MemberRepository(Protocol):
    async def get(self, trip_id: UUID, user_id: str) -> TripMember | None: ...
    async def list(self, trip_id: UUID) -> list[TripMember]: ...
    async def add(self, member: TripMember) -> None: ...
    async def update_role(self, trip_id: UUID, user_id: str, role: Role) -> None: ...
    async def remove(self, trip_id: UUID, user_id: str) -> None: ...


class ItineraryRepository(Protocol):
    async def add(self, item: ItineraryItem) -> None: ...
    async def get(self, item_id: UUID) -> ItineraryItem | None: ...
    async def list_for_trip(self, trip_id: UUID) -> list[ItineraryItem]: ...
    async def save(self, item: ItineraryItem) -> None: ...
    async def remove(self, item_id: UUID) -> None: ...
    async def set_positions(self, positions: dict[UUID, int]) -> None: ...


class BookingRepository(Protocol):
    async def add(self, booking: Booking) -> None:
        """Raises ``Conflict('BOOKING_IN_PROGRESS')`` if the item already has an active
        attempt (draft, submitted, unknown, pending or confirmed)."""
        ...

    async def get_active_for_item(self, item_id: UUID) -> Booking | None: ...
    async def list_stale_submitted(self, before: datetime) -> list[Booking]: ...
    async def get(self, booking_id: UUID) -> Booking | None: ...
    async def list_for_trip(self, trip_id: UUID) -> list[Booking]: ...

    async def save(self, booking: Booking) -> bool:
        """Persists a state change if nobody else changed the booking meanwhile
        (``booking.version`` is the version read). Returns False on conflict."""
        ...


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> None:
        """Raises ``Conflict('DOCUMENT_IN_PROGRESS')`` if a document for the same trip
        version is already waiting or in progress."""
        ...

    async def get(self, document_id: UUID) -> Document | None: ...
    async def list_for_trip(self, trip_id: UUID) -> list[Document]: ...
    async def list_all(self) -> list[Document]: ...

    async def save(self, document: Document, *, expected_status: str) -> bool:
        """Persists a state change only if the stored status is still ``expected_status``
        (so two actors cannot both move the same document). False otherwise."""
        ...


class JobRepository(Protocol):
    async def add(self, job: Job) -> None: ...

    async def enqueue(self, job: Job) -> bool:
        """Adds the job unless an active (queued/running) job has the same dedupe key."""
        ...

    async def claim(self, now: datetime, lease_until: datetime, limit: int) -> list[Job]:
        """Atomically takes up to ``limit`` due jobs (queued and due, or running with an
        expired lease) and marks them running until ``lease_until``. Concurrent callers
        never receive the same job."""
        ...
    async def get(self, job_id: UUID) -> Job | None: ...
    async def save(self, job: Job) -> None: ...


class UnitOfWork(Protocol):
    users: UserRepository
    trips: TripRepository
    members: MemberRepository
    changes: TripChangeRepository
    itinerary: ItineraryRepository
    bookings: BookingRepository
    documents: DocumentRepository
    jobs: JobRepository

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
