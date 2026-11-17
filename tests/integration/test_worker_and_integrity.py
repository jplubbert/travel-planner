"""Job queue guarantees and database-level booking integrity (ADR 0003), on PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.sqlalchemy.uow import SqlUnitOfWork
from app.domain.jobs import Job, JobStatus

NOW = datetime.now(UTC)


def job(n: int, **overrides) -> Job:
    values = {"id": uuid4(), "kind": "sync_booking", "status": JobStatus.QUEUED,
              "run_after": NOW - timedelta(seconds=1), "created_at": NOW, "updated_at": NOW,
              "payload": {"bookingId": str(uuid4())}, "dedupe_key": f"test:{n}"}
    return Job(**{**values, **overrides})


async def add_jobs(app, jobs):
    async with SqlUnitOfWork(app.state.sessions) as uow:
        for j in jobs:
            assert await uow.jobs.enqueue(j)
        await uow.commit()


async def claim(app, limit, now=None):
    now = now or datetime.now(UTC)
    async with SqlUnitOfWork(app.state.sessions) as uow:
        claimed = await uow.jobs.claim(now, now + timedelta(minutes=2), limit)
        await asyncio.sleep(0.2)  # keep the row locks while the other worker claims
        await uow.commit()
    return claimed


async def test_two_workers_never_claim_the_same_job(app):
    await add_jobs(app, [job(n) for n in range(12)])
    a, b = await asyncio.gather(claim(app, 5), claim(app, 5))
    ids_a, ids_b = {j.id for j in a}, {j.id for j in b}
    assert ids_a and ids_b and not ids_a & ids_b
    assert len(ids_a) + len(ids_b) == 10
    rest = await claim(app, 10)
    assert len(rest) == 2 and not {j.id for j in rest} & (ids_a | ids_b)
    assert await claim(app, 10) == []  # everything is running under a lease


async def test_a_job_whose_worker_died_is_reclaimed_after_its_lease(app):
    await add_jobs(app, [job(1)])
    [first] = await claim(app, 1)
    assert first.status == JobStatus.RUNNING and first.attempts == 1
    assert await claim(app, 1) == []  # lease still valid
    [again] = await claim(app, 1, now=datetime.now(UTC) + timedelta(minutes=5))
    assert again.id == first.id and again.attempts == 2


async def test_future_jobs_wait_until_due(app):
    await add_jobs(app, [job(1, run_after=datetime.now(UTC) + timedelta(hours=1))])
    assert await claim(app, 10) == []


async def test_only_one_active_job_per_dedupe_key(app):
    async with SqlUnitOfWork(app.state.sessions) as uow:
        assert await uow.jobs.enqueue(job(1, dedupe_key="sync_booking:x"))
        assert not await uow.jobs.enqueue(job(2, dedupe_key="sync_booking:x"))
        await uow.commit()
    [claimed] = await claim(app, 10)
    claimed.move(JobStatus.SUCCEEDED, datetime.now(UTC))
    async with SqlUnitOfWork(app.state.sessions) as uow:
        await uow.jobs.save(claimed)
        # A finished job does not block a new one for the same key.
        assert await uow.jobs.enqueue(job(3, dedupe_key="sync_booking:x"))
        await uow.commit()


async def test_worker_survives_a_failing_job_and_retries_it(app, make_worker):
    await add_jobs(app, [job(1, kind="no_such_kind")])
    worker = make_worker(retry_base_seconds=3600, retry_max_seconds=3600)
    assert await worker.run_once() == 1
    async with SqlUnitOfWork(app.state.sessions) as uow:
        row = (await uow.session.execute(text(
            "SELECT status, last_error, run_after > now() FROM jobs"))).one()
    assert row[0] == "queued" and "unknown job kind" in row[1] and row[2]


# --- Booking rows: accepted data and terminal states are final at the database --------------


@pytest.fixture
async def confirmed_booking(app, api, shared_trip, mock_control):
    ana = api.as_("ana")
    view = (await ana.post(f"/trips/{shared_trip['id']}/itinerary/items",
                           json={"version": 1, "offerId": "offer-lis-hotel-central-p2"})).json()
    item = view["items"][0]
    r = await ana.post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/bookings",
        json={"expectedRevision": item["quote"]["revision"],
              "expectedPrice": item["quote"]["price"]})
    assert r.json()["status"] == "confirmed"
    return r.json()["bookingId"]


@pytest.mark.parametrize("change", [
    "status = 'failed'",
    "accepted_price = 1",
    "accepted_offer = '{}'::jsonb",
    "provider_booking_id = 'other'",
    "idempotency_key = 'other'",
])
async def test_database_refuses_to_alter_a_confirmed_booking(app, confirmed_booking, change):
    async with SqlUnitOfWork(app.state.sessions) as uow:
        with pytest.raises(DBAPIError, match="cannot change|immutable"):
            await uow.session.execute(text(f"UPDATE bookings SET {change} WHERE id = :id"),
                                      {"id": confirmed_booking})


async def test_database_allows_harmless_updates(app, confirmed_booking):
    async with SqlUnitOfWork(app.state.sessions) as uow:
        await uow.session.execute(text(
            "UPDATE bookings SET last_error = NULL, updated_at = now() WHERE id = :id"),
            {"id": confirmed_booking})
        await uow.commit()


async def test_database_allows_one_active_attempt_per_item_only(app, confirmed_booking):
    async with SqlUnitOfWork(app.state.sessions) as uow:
        row = (await uow.session.execute(text(
            "SELECT trip_id, itinerary_item_id FROM bookings WHERE id = :id"),
            {"id": confirmed_booking})).one()
        with pytest.raises(DBAPIError, match="uq_bookings_one_active_per_item"):
            await uow.session.execute(text(
                "INSERT INTO bookings (id, trip_id, itinerary_item_id, offer_id, "
                "expected_revision, expected_price, client_reference, idempotency_key, "
                "requested_by, status, version, created_at, updated_at) VALUES "
                "(:id, :trip, :item, 'o', 'r', :price, 'ref-x', 'key-x', 'ana', 'draft', 1, "
                "now(), now())"),
                {"id": uuid4(), "trip": row[0], "item": row[1], "price": Decimal("1")})
