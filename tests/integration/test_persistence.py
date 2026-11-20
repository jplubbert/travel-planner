"""Schema/migration consistency and repository round-trips against PostgreSQL."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
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
# Provider-local time: the +01:00 offset must survive a round trip (local dates matter).
STARTS = datetime(2030, 6, 11, 10, tzinfo=timezone(timedelta(hours=1)))


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


# --- Compare-and-set in the repository alone (no _start version check in front) ---------------


async def saved_version(uow_factory, trip_id) -> tuple[int, str]:
    async with uow_factory() as uow:
        trip = await uow.trips.get(trip_id)
        assert trip is not None
        return trip.version, trip.name


async def test_update_writes_only_when_the_expected_version_matches(uow_factory):
    trip = await make_trip(uow_factory)  # version 1
    async with uow_factory() as uow:
        assert await uow.trips.update(replace(trip, name="A", version=2), expected_version=1)
        await uow.commit()
    async with uow_factory() as uow:  # stale: version 1 is gone
        assert not await uow.trips.update(replace(trip, name="B", version=2),
                                          expected_version=1)
        await uow.commit()
    assert await saved_version(uow_factory, trip.id) == (2, "A")


async def test_bump_version_claims_each_version_once(uow_factory):
    trip = await make_trip(uow_factory)
    async with uow_factory() as uow:
        assert await uow.trips.bump_version(trip.id, expected_version=1, now=NOW)
        assert not await uow.trips.bump_version(trip.id, expected_version=1, now=NOW)
        await uow.commit()
    assert await saved_version(uow_factory, trip.id) == (2, "T")


@pytest.mark.parametrize("operation", ["update", "bump_version"])
async def test_concurrent_writers_on_one_version_exactly_one_wins(uow_factory, operation):
    """Two transactions expecting version 1 at once. The second blocks on the row lock of
    the first; once the first commits it re-checks the condition and finds version 2."""
    trip = await make_trip(uow_factory)

    async def write(uow, name):
        if operation == "update":
            return await uow.trips.update(replace(trip, name=name, version=2),
                                          expected_version=1)
        return await uow.trips.bump_version(trip.id, expected_version=1, now=NOW)

    async with uow_factory() as first, uow_factory() as second:
        assert await write(first, "first")
        late = asyncio.create_task(write(second, "second"))
        await asyncio.sleep(0.3)
        assert not late.done()  # waiting for the first transaction's row lock
        await first.commit()
        assert await asyncio.wait_for(late, timeout=5) is False
        await second.commit()
    version, name = await saved_version(uow_factory, trip.id)
    assert version == 2 and name in ("first", "T")


async def test_database_enforces_a_single_organizer(uow_factory):
    trip = await make_trip(uow_factory)
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.members.add(TripMember(trip.id, "beto", Role.ORGANIZER, NOW))


async def test_itinerary_item_round_trip(uow_factory):
    trip = await make_trip(uow_factory)
    item = ItineraryItem(
        id=uuid4(), trip_id=trip.id, position=0, offer_id="offer-lis-tram-tour-p2",
        provider_id="supplier-a", category="activity", title="Tram", description="Tour",
        destination="Lisbon", origin=None, starts_at=STARTS, ends_at=STARTS + timedelta(hours=2),
        travelers=2, price=Decimal("56.00"), offer_revision="r1", available=True,
        valid_until=NOW, quoted_at=NOW, added_by="ana", created_at=NOW,
        offer_snapshot={"startsAt": STARTS.isoformat(),
                        "endsAt": (STARTS + timedelta(hours=2)).isoformat()},
        restrictions=("Comfortable shoes",),
    )
    async with uow_factory() as uow:
        await uow.itinerary.add(item)
        await uow.commit()
    async with uow_factory() as uow:
        stored = await uow.itinerary.get(item.id)
        assert stored == item and stored.starts_at.utcoffset() == timedelta(hours=1)
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


async def test_python_none_is_sql_null_so_the_snapshot_constraint_holds(uow_factory):
    """Regression: JSONB stored None as JSON null, which satisfied "IS NOT NULL"."""
    trip = await make_trip(uow_factory)
    async with uow_factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.bookings.add(Booking(
                id=uuid4(), trip_id=trip.id, itinerary_item_id=None, offer_id="o",
                expected_revision="r", expected_price=Decimal("1.00"), client_reference="r3",
                idempotency_key="k3", requested_by="ana", status=BookingStatus.CONFIRMED,
                created_at=NOW, updated_at=NOW, provider_booking_id="b",
                accepted_offer=None, accepted_price=Decimal("1.00"),
            ))


async def test_document_round_trip_and_complete_requires_file(uow_factory):
    trip = await make_trip(uow_factory)
    doc = Document(uuid4(), trip.id, 1, "ana", DocumentStatus.WAITING, NOW, NOW,
                   snapshot={"trip": {"name": "T"}}, snapshot_at=NOW, snapshot_sha256="0" * 64)
    async with uow_factory() as uow:
        await uow.documents.add(doc)
        await uow.commit()
    async with uow_factory() as uow:
        assert await uow.documents.get(doc.id) == doc
        doc.move(DocumentStatus.IN_PROGRESS, NOW)
        doc.move(DocumentStatus.COMPLETE, NOW)  # but no file recorded
        with pytest.raises(IntegrityError):
            await uow.documents.save(doc, expected_status=DocumentStatus.WAITING)


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



async def test_migration_0003_backfills_existing_itinerary_rows(migration_db):
    """Changing the data structure keeps existing data: rows created before 0003 get a
    snapshot, availability and a starting change-history entry."""
    url, migrate = migration_db
    await migrate("0002")
    engine = create_async_engine(url, poolclass=NullPool)
    trip_id, item_id = uuid4(), uuid4()
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO trips VALUES (:id, 'Old trip', ARRAY['Lisbon'], '2030-06-10', "
            "'2030-06-14', 2, 1000, 'USD', 4, 'ana', now(), now())"), {"id": trip_id})
        await conn.execute(text(
            "INSERT INTO itinerary_items (id, trip_id, position, offer_id, provider_id, "
            "category, title, destination, origin, starts_at, ends_at, travelers, price, "
            "offer_revision, valid_until, quoted_at, restrictions, added_by, created_at) "
            "VALUES (:id, :trip, 0, 'offer-old-p2', 'supplier-a', 'activity', 'Old tour', "
            "'Lisbon', NULL, '2030-06-11T10:00:00+01:00', '2030-06-11T12:00:00+01:00', 2, "
            "56.5, 'r1', '2030-06-01T12:15:00Z', now(), '[\"x\"]', 'ana', now())"),
            {"id": item_id, "trip": trip_id})
    await migrate("head")

    sessions = session_factory(engine)
    async with SqlUnitOfWork(sessions) as uow:
        item = await uow.itinerary.get(item_id)
        assert item.available is True and item.description == "Old tour"
        assert item.offer_snapshot["price"] == {"amount": "56.50", "currency": "USD"}
        assert item.offer_snapshot["startsAt"] == "2030-06-11T09:00:00+00:00"  # same instant
        assert item.starts_at == datetime(2030, 6, 11, 9, tzinfo=UTC)
        assert item.restrictions == ("x",) and "origin" not in item.offer_snapshot
        [change] = await uow.changes.list_since(trip_id, 0)
        assert (change.version, change.kind) == (4, "history_start")
    await engine.dispose()
