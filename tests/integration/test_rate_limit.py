"""Shared provider rate limit (provider_rate_limits): a 429's Retry-After seen by any
process holds for every API and worker process, against PostgreSQL and the mock."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.adapters.sqlalchemy.rate_limits import SqlProviderRateLimits
from app.domain.rate_limit import PROVIDER
from tests.integration.test_us07_us08_bookings import (
    HOTEL,
    add_item,
    book,
    bookings,
    lost_response_app,
    scenario,
)

TRAM = "offer-lis-tram-tour-p2"  # supplier-a, like HOTEL


class CountingProvider:
    """The real HTTP client, counting the calls that actually reach it."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.calls = inner, 0

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.inner, name)

        async def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return await method(*args, **kwargs)
        return counted


async def stored_not_before(app) -> datetime | None:
    return await SqlProviderRateLimits(app.state.sessions).not_before(PROVIDER)


# --- The stored wait is never shortened -----------------------------------------------------


async def test_hold_until_keeps_the_longest_wait(app):
    limits = SqlProviderRateLimits(app.state.sessions)
    now = datetime.now(UTC)
    assert await limits.not_before(PROVIDER) is None
    long, short = now + timedelta(seconds=60), now + timedelta(seconds=5)
    assert await limits.hold_until(PROVIDER, long) == long
    assert await limits.hold_until(PROVIDER, short) == long  # not shortened
    assert await limits.not_before(PROVIDER) == long
    longer = now + timedelta(seconds=120)
    assert await limits.hold_until(PROVIDER, longer) == longer  # extended


async def test_concurrent_429s_keep_the_longest_wait(app):
    limits = SqlProviderRateLimits(app.state.sessions)
    now = datetime.now(UTC)
    waits = [now + timedelta(seconds=s) for s in (3, 90, 1, 45, 7, 60)]
    await asyncio.gather(*(limits.hold_until(PROVIDER, w) for w in waits))
    assert await limits.not_before(PROVIDER) == max(waits)


async def test_a_shorter_429_does_not_shorten_the_wait(api, shared_trip, mock_control):
    body = {"category": "accommodation", "destination": "Lisbon", "providerId": "supplier-b"}
    url = f"/trips/{shared_trip['id']}/offers/search"
    await scenario(mock_control, "supplier-b", "rate_limited", retryAfterSeconds=60)
    first = await api.as_("ana").post(url, json=body)
    assert first.status_code == 503 and first.headers["Retry-After"] == "60"
    held = await stored_not_before(api.app)

    # Another process's call with an older view gets a 2 s Retry-After: the 60 s stay.
    limits = SqlProviderRateLimits(api.app.state.sessions)
    assert await limits.hold_until(PROVIDER, datetime.now(UTC) + timedelta(seconds=2)) == held
    assert await stored_not_before(api.app) == held


# --- Workers: one gets the 429, the other does not call before not_before --------------------


async def job_of(app, booking_id: str) -> dict:
    async with app.state.sessions() as session:
        row = (await session.execute(text(
            "SELECT status, run_after FROM jobs WHERE payload->>'bookingId' = :id"
        ), {"id": booking_id})).mappings().one()
    return dict(row)


async def test_a_429_in_one_worker_stops_the_other_until_not_before(
    api, make_app, mock_provider_url, shared_trip, mock_control, make_worker, provider_client
):
    ana = api.as_("ana")
    lossy = lost_response_app(make_app, mock_control, mock_provider_url)
    hotel, tram = await add_item(ana, shared_trip, HOTEL), await add_item(ana, shared_trip, TRAM)
    first = (await book(api.as_("ana", app=lossy), shared_trip, hotel)).json()
    await mock_control.delete("/scenario")  # lost_response_app turns it on before each POST
    second = (await book(api.as_("ana", app=lossy), shared_trip, tram)).json()
    assert first["status"] == second["status"] == "unknown"  # both have a sync job due
    before = {b["bookingId"]: b for b in await bookings(ana, shared_trip)}

    await scenario(mock_control, "supplier-a", "rate_limited", retryAfterSeconds=30)
    seen_by_one, seen_by_two = CountingProvider(provider_client), CountingProvider(provider_client)
    one = make_worker("worker-1", provider=seen_by_one)
    one.batch_size = 1  # only the first booking's job
    assert await one.run_once() == 1
    assert seen_by_one.calls == 1  # the lookup by reference, answered 429
    not_before = await stored_not_before(api.app)
    assert not_before is not None
    assert 28 <= (not_before - datetime.now(UTC)).total_seconds() <= 30

    await mock_control.delete("/scenario")  # the provider would answer now
    two = make_worker("worker-2", provider=seen_by_two)
    assert await two.run_once() == 1  # claims the second booking's job...
    assert seen_by_two.calls == 0  # ...and does not call the provider
    after = {b["bookingId"]: b for b in await bookings(ana, shared_trip)}
    assert after[second["bookingId"]] == before[second["bookingId"]]  # untouched
    job = await job_of(api.app, second["bookingId"])
    assert job["status"] == "queued"
    assert abs((job["run_after"] - not_before).total_seconds()) < 1  # rescheduled for it
    assert await two.run_once() == 0  # nothing due before not_before


# --- User requests do not wait ----------------------------------------------------------------


async def test_user_search_during_the_limit_answers_at_once(api, shared_trip, mock_control):
    url = f"/trips/{shared_trip['id']}/offers/search"
    await scenario(mock_control, "supplier-b", "rate_limited", retryAfterSeconds=30)
    r = await api.as_("ana").post(url, json={"category": "accommodation",
                                             "destination": "Lisbon",
                                             "providerId": "supplier-b"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVIDER_RATE_LIMITED"
    await mock_control.delete("/scenario")  # the provider would answer this search now

    started = time.monotonic()
    r = await api.as_("carla").post(url, json={"category": "activity",
                                               "destination": "Lisbon"})
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert r.status_code == 503
    body = r.json()
    assert body["outcome"] == "failed" and body["offers"] == []
    assert body["error"]["code"] == "PROVIDER_RATE_LIMITED"
    assert body["error"]["providerRequestId"] is None  # it never reached the provider
    retry_after = body["error"]["retryAfterSeconds"]
    assert 1 <= retry_after <= 30 and r.headers["Retry-After"] == str(retry_after)
