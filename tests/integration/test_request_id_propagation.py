"""The X-Request-Id of the request that created a job reaches the job, the worker's logs and
the provider calls the job makes, so a failed background operation can be traced back to the
user action that started it."""

import logging
from typing import Any

from sqlalchemy import select

from app.adapters.sqlalchemy.models import JobRow
from app.observability import request_id_var

HOTEL = "offer-lis-hotel-central-p2"


class DyingProvider:
    """The real HTTP client, but the booking POST raises before reaching the provider."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def create_booking(self, request: Any, *, idempotency_key: str) -> Any:
        from app.domain.provider import ProviderTimeout
        raise ProviderTimeout("no answer")  # delivery uncertain: the booking becomes unknown


async def add_item(client: Any, trip: dict) -> dict:
    version = (await client.get(f"/trips/{trip['id']}")).json()["version"]
    r = await client.post(f"/trips/{trip['id']}/itinerary/items",
                          json={"version": version, "offerId": HOTEL})
    assert r.status_code == 201, r.text
    return r.json()["items"][0]


async def job_request_ids(app: Any, kind: str) -> list[str | None]:
    async with app.state.sessions() as session:
        rows = await session.scalars(select(JobRow).where(JobRow.kind == kind))
        return [row.request_id for row in rows]


def messages(caplog: Any, logger: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger]


async def test_booking_job_carries_the_request_id_into_worker_and_provider_logs(
    api, app, make_app, shared_trip, mock_control, provider_client, make_worker, caplog
):
    item = await add_item(api.as_("ana"), shared_trip)
    dying = make_app(provider=DyingProvider(provider_client))
    client = api.as_("ana", app=dying)
    r = await client.post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/bookings",
        headers={"X-Request-Id": "trace-booking-123"},
        json={"expectedRevision": item["quote"]["revision"],
              "expectedPrice": item["quote"]["price"]})
    assert r.status_code == 201 and r.json()["status"] == "unknown"
    assert r.headers["X-Request-Id"] == "trace-booking-123"
    assert await job_request_ids(app, "sync_booking") == ["trace-booking-123"]

    caplog.set_level(logging.INFO)
    caplog.clear()
    assert await make_worker().run_once() == 1
    assert any("sync_booking" in m and "request_id=trace-booking-123" in m
               for m in messages(caplog, "app.worker"))
    # The reconciliation's provider call is logged under the same id.
    assert any("getBookingByReference" in m and "request_id=trace-booking-123" in m
               for m in messages(caplog, "app.provider"))
    assert request_id_var.get() is None  # nothing leaks into the next job


async def test_document_job_carries_the_request_id(
    api, app, shared_trip, make_worker, caplog
):
    r = await api.as_("ana").post(f"/trips/{shared_trip['id']}/documents",
                                  headers={"X-Request-Id": "trace-doc-456"})
    assert r.status_code == 202, r.text
    assert await job_request_ids(app, "generate_document") == ["trace-doc-456"]

    caplog.set_level(logging.INFO)
    caplog.clear()
    assert await make_worker().run_once() == 1
    assert any("generate_document" in m and "request_id=trace-doc-456" in m
               for m in messages(caplog, "app.worker"))


async def test_jobs_created_by_the_worker_sweep_have_no_request_id(
    api, app, make_app, shared_trip, mock_control, provider_client, make_worker
):
    """A submission whose sender died is found by the worker's sweep, not by a request: its
    job has no originating id (the booking is traced by its clientReference instead)."""

    class Killed(DyingProvider):
        async def create_booking(self, request: Any, *, idempotency_key: str) -> Any:
            raise RuntimeError("process killed")

    item = await add_item(api.as_("ana"), shared_trip)
    r = await api.as_("ana", app=make_app(provider=Killed(provider_client))).post(
        f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/bookings",
        headers={"X-Request-Id": "trace-dead-789"},
        json={"expectedRevision": item["quote"]["revision"],
              "expectedPrice": item["quote"]["price"]})
    assert r.status_code == 500
    assert await job_request_ids(app, "sync_booking") == []

    await make_worker().run_once()  # the start-up sweep enqueues and runs it
    assert await job_request_ids(app, "sync_booking") == [None]
