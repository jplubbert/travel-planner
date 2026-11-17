"""Schema/migration consistency and repository round-trips against PostgreSQL."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.sqlalchemy.models import Base
from app.adapters.sqlalchemy.uow import SqlUnitOfWork, session_factory
from app.domain.access import Role, TripMember
from app.domain.bookings import Booking, BookingStatus
from app.domain.documents import Document, DocumentStatus
from app.domain.itinerary import ItineraryItem
from app.domain.jobs import Job, JobStatus
from app.domain.trips import Trip

NOW = datetime(2030, 6, 1, 12, tzinfo=UTC)


@pytest.fixture
async def engine(database_url):
    eng = create_async_engine(database_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@pytest.fixture
def uow_factory(engine):
    sessions = session_factory(engine)
    return lambda: SqlUnitOfWork(sessions)


async def test_migrations_match_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata))
    assert diff == []


async def make_trip(uow_factory) -> Trip:
    trip = Trip(uuid4(), "T", ("Lisbon",), date(2030, 6, 10), date(2030, 6, 14), 2,
                Decimal("1000.00"), 1, "ana", NOW, NOW)
    async with uow_factory() as uow:
        await uow.trips.add(trip)
        await uow.members.add(TripMember(trip.id, "ana", Role.ORGANIZER, NOW))
        await uow.commit()
    return trip


async def test_uncommitted_work_is_rolled_back(uow_factory):
    trip_id = uuid4()
    async with uow_factory() as uow:
        await uow.trips.add(Trip(trip_id, "T", ("Lisbon",), date(2030, 6, 10),
                                 date(2030, 6, 14), 2, Decimal("1"), 1, "ana", NOW, NOW))
        # no commit: e.g. the request failed before finishing
    async with uow_factory() as uow:
        assert await uow.trips.get(trip_id) is None


async def test_database_enforces_a_single_organizer(uow_factory):
    trip = await make_trip(uow_factory)
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.members.add(TripMember(trip.id, "beto", Role.ORGANIZER, NOW))


async def test_itinerary_item_round_trip(uow_factory):
    trip = await make_trip(uow_factory)
    item = ItineraryItem(
        id=uuid4(), trip_id=trip.id, position=0, offer_id="offer-lis-tram-tour-p2",
        provider_id="supplier-a", category="activity", title="Tram", destination="Lisbon",
        origin=None, starts_at=NOW, ends_at=NOW + timedelta(hours=2), travelers=2,
        price=Decimal("56.00"), offer_revision="r1", valid_until=NOW, quoted_at=NOW,
        added_by="ana", created_at=NOW, restrictions=("Comfortable shoes",),
    )
    async with uow_factory() as uow:
        await uow.itinerary.add(item)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.itinerary.get(item.id) == item
        assert await uow.itinerary.list_for_trip(trip.id) == [item]


async def test_booking_round_trip_and_optimistic_save(uow_factory):
    trip = await make_trip(uow_factory)
    booking = Booking(
        id=uuid4(), trip_id=trip.id, itinerary_item_id=None, offer_id="offer-x-p2",
        expected_revision="r1", expected_price=Decimal("670.00"), client_reference="ref-1",
        idempotency_key="key-1", requested_by="ana", status=BookingStatus.DRAFT,
        created_at=NOW, updated_at=NOW,
    )
    async with uow_factory() as uow:
        await uow.bookings.add(booking)
        await uow.commit()

    async with uow_factory() as a, uow_factory() as b:
        first, second = await a.bookings.get(booking.id), await b.bookings.get(booking.id)
        first.submit(NOW)
        assert await a.bookings.save(first)
        await a.commit()
        second.submit(NOW)
        assert not await b.bookings.save(second)  # stale copy: refused

    async with uow_factory() as uow:
        stored = await uow.bookings.get(booking.id)
        assert stored.status == BookingStatus.SUBMITTED and stored.version == 2
        assert await uow.bookings.list_for_trip(trip.id) == [stored]


async def test_database_refuses_confirmed_booking_without_snapshot(uow_factory):
    trip = await make_trip(uow_factory)
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.bookings.add(Booking(
                id=uuid4(), trip_id=trip.id, itinerary_item_id=None, offer_id="o",
                expected_revision="r", expected_price=Decimal("1.00"), client_reference="r2",
                idempotency_key="k2", requested_by="ana", status=BookingStatus.CONFIRMED,
                created_at=NOW, updated_at=NOW,
            ))


async def test_document_round_trip_and_complete_requires_file(uow_factory):
    trip = await make_trip(uow_factory)
    doc = Document(uuid4(), trip.id, 1, "ana", DocumentStatus.WAITING, NOW, NOW)
    async with uow_factory() as uow:
        await uow.documents.add(doc)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.documents.get(doc.id) == doc
        doc.move(DocumentStatus.IN_PROGRESS, NOW)
        doc.move(DocumentStatus.COMPLETE, NOW)  # but no file recorded
        with pytest.raises(IntegrityError):
            await uow.documents.save(doc)


async def test_job_round_trip(uow_factory):
    job = Job(uuid4(), "reconcile_booking", JobStatus.QUEUED, NOW, NOW, NOW,
              payload={"bookingId": "b"}, dedupe_key="reconcile:b")
    async with uow_factory() as uow:
        await uow.jobs.add(job)
        await uow.commit()
    async with uow_factory() as uow:
        stored = await uow.jobs.get(job.id)
        assert stored == job
        stored.move(JobStatus.RUNNING, NOW)
        stored.attempts = 1
        await uow.jobs.save(stored)
        await uow.commit()
    async with uow_factory() as uow:
        assert (await uow.jobs.get(job.id)).status == JobStatus.RUNNING
