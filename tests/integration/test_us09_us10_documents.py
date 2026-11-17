"""US-09 (generate a trip document) and US-10 (access the right document), against
PostgreSQL, the provider mock and a real local document storage directory."""

import asyncio
import hashlib
import os
import time
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.document_html import HtmlDocumentRenderer
from app.adapters.local_storage import LocalDocumentStorage
from app.adapters.sqlalchemy.uow import SqlUnitOfWork

HOTEL = "offer-lis-hotel-central-p2"
SURF = "offer-lis-surf-lesson-p2"  # its booking fails


class ProcessKilled(BaseException):
    """Escapes the worker's error handling, like the process being killed."""


async def add_item(client, trip, offer_id):
    version = (await client.get(f"/trips/{trip['id']}")).json()["version"]
    r = await client.post(f"/trips/{trip['id']}/itinerary/items",
                          json={"version": version, "offerId": offer_id})
    assert r.status_code == 201, r.text
    return next(i for i in r.json()["items"] if i["offerId"] == offer_id)


async def book(client, trip, item):
    r = await client.post(f"/trips/{trip['id']}/itinerary/items/{item['itemId']}/bookings",
                          json={"expectedRevision": item["quote"]["revision"],
                                "expectedPrice": item["quote"]["price"]})
    return r.json()


async def request_doc(client, trip):
    return await client.post(f"/trips/{trip['id']}/documents")


async def doc(client, trip, doc_id):
    r = await client.get(f"/trips/{trip['id']}/documents/{doc_id}")
    assert r.status_code == 200, r.text
    return r.json()


def download(client, trip, doc_id):
    return client.get(f"/trips/{trip['id']}/documents/{doc_id}/download")


async def generated(api, make_worker, trip, user="ana"):
    r = await request_doc(api.as_(user), trip)
    assert r.status_code in (200, 202), r.text
    await make_worker().run_once()
    return await doc(api.as_(user), trip, r.json()["documentId"])


# --- US-09: asynchronous generation with visible status --------------------------------------


async def test_request_returns_202_immediately_and_the_worker_completes_it(
    api, shared_trip, mock_control, make_worker, storage_dir
):
    beto = api.as_("beto")
    started = time.monotonic()
    r = await request_doc(beto, shared_trip)
    assert r.status_code == 202 and time.monotonic() - started < 1
    d = r.json()
    assert d["status"] == "waiting" and d["ready"] is False and d["downloadUrl"] is None
    assert d["tripVersion"] == 1 and d["requestedBy"] == "beto"
    assert r.headers["Location"] == f"/trips/{shared_trip['id']}/documents/{d['documentId']}"
    assert (await download(beto, shared_trip, d["documentId"])).json()["code"] == \
        "DOCUMENT_NOT_READY"

    assert await make_worker().run_once() == 1
    done = await doc(api.as_("carla"), shared_trip, d["documentId"])  # viewer reads status
    assert done["status"] == "complete" and done["ready"] is True
    assert done["downloadUrl"].endswith("/download")
    path = storage_dir / str(shared_trip["id"]) / f"{d['documentId']}.html"
    assert path.stat().st_size == done["sizeBytes"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == done["sha256"]


async def test_document_contains_itinerary_costs_and_booking_statuses(
    api, shared_trip, mock_control, make_worker
):
    ana = api.as_("ana")
    hotel = await add_item(ana, shared_trip, HOTEL)
    surf = await add_item(ana, shared_trip, SURF)
    assert (await book(ana, shared_trip, hotel))["status"] == "confirmed"
    assert (await book(ana, shared_trip, surf))["status"] == "failed"
    d = await generated(api, make_worker, shared_trip)

    r = await download(api.as_("carla"), shared_trip, d["documentId"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["X-Content-SHA256"] == hashlib.sha256(r.content).hexdigest() == d["sha256"]
    assert r.headers["X-Trip-Version"] == str(d["tripVersion"]) == "3"
    assert "attachment" in r.headers["Content-Disposition"]
    html = r.text
    assert "Iberia + Paris" in html and "Trip version 3" in html
    assert d["snapshotAt"][:19] in html  # the point in time it represents
    assert hotel["title"] in html and surf["title"] in html
    assert "670.00 USD" in html  # confirmed, accepted price
    assert "confirmed (booked)" in html and ">failed<" in html
    assert "Estimated" in html and "Confirmed" in html and "4500.00 USD" in html
    assert "<script" not in html and "http" not in html.split("<body>")[1]  # self-contained


async def test_repeated_requests_while_running_return_the_same_document(
    api, shared_trip, mock_control, make_worker, app
):
    responses = await asyncio.gather(*(request_doc(api.as_(u), shared_trip)
                                       for u in ("ana", "beto", "ana", "beto", "ana")))
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["documentId"] for r in responses}) == 1
    async with SqlUnitOfWork(app.state.sessions) as uow:
        jobs = (await uow.session.execute(text(
            "SELECT count(*) FROM jobs WHERE kind = 'generate_document'"))).scalar_one()
    assert jobs == 1
    assert len((await api.as_("ana").get(f"/trips/{shared_trip['id']}/documents")).json()) == 1


async def test_complete_document_for_the_same_content_is_reused(
    api, shared_trip, mock_control, make_worker
):
    first = await generated(api, make_worker, shared_trip)
    r = await request_doc(api.as_("ana"), shared_trip)
    assert r.status_code == 200 and r.json()["documentId"] == first["documentId"]

    await add_item(api.as_("ana"), shared_trip, HOTEL)  # the trip changes: new document
    r = await request_doc(api.as_("ana"), shared_trip)
    assert r.status_code == 202 and r.json()["documentId"] != first["documentId"]
    assert r.json()["tripVersion"] == 2


async def test_failed_generation_is_reported_and_can_be_retried(
    api, shared_trip, mock_control, make_worker
):
    class BrokenRenderer:
        media_type = "text/html"

        def render(self, snapshot: dict[str, Any]) -> bytes:
            raise RuntimeError("renderer crashed")

    r = await request_doc(api.as_("ana"), shared_trip)
    doc_id = r.json()["documentId"]
    broken = make_worker(renderer=BrokenRenderer())
    for _ in range(3):  # document_max_attempts
        await broken.run_once()
    failed = await doc(api.as_("ana"), shared_trip, doc_id)
    assert failed["status"] == "failed" and failed["ready"] is False
    assert "after 3 attempts" in failed["error"] and "renderer crashed" in failed["error"]
    assert (await download(api.as_("ana"), shared_trip, doc_id)).status_code == 409

    url = f"/trips/{shared_trip['id']}/documents/{doc_id}/retry"
    assert (await api.as_("carla").post(url)).status_code == 403  # viewers cannot
    r = await api.as_("beto").post(url)
    assert r.status_code == 202 and r.json()["status"] == "waiting"
    await make_worker().run_once()
    assert (await doc(api.as_("ana"), shared_trip, doc_id))["status"] == "complete"
    r = await api.as_("beto").post(url)
    assert r.status_code == 409 and r.json()["code"] == "DOCUMENT_NOT_RETRYABLE"


# --- Interruptions ---------------------------------------------------------------------------


@pytest.mark.parametrize("dies", ["while_writing", "after_writing_before_marking"])
async def test_worker_killed_mid_generation_is_recovered_by_lease(
    api, shared_trip, mock_control, make_worker, storage_dir, dies
):
    real = LocalDocumentStorage(storage_dir)

    class DyingStorage:
        def __getattr__(self, name):
            return getattr(real, name)

        async def write(self, key: str, content: bytes) -> None:
            if dies == "while_writing":
                # A partial temp file is all that reaches the disk.
                (storage_dir / key).parent.mkdir(parents=True, exist_ok=True)
                (storage_dir / f"{key}.tmp-crashed").write_bytes(content[:10])
            else:
                await real.write(key, content)
            raise ProcessKilled()

    doc_id = (await request_doc(api.as_("ana"), shared_trip)).json()["documentId"]
    with pytest.raises(ProcessKilled):
        await make_worker(storage=DyingStorage(), lease_seconds=0.2).run_once()

    # Interrupted: never presented as ready, not downloadable.
    d = await doc(api.as_("ana"), shared_trip, doc_id)
    assert d["status"] == "in_progress" and d["ready"] is False and d["sha256"] is None
    assert (await download(api.as_("ana"), shared_trip, doc_id)).status_code == 409

    survivor = make_worker()
    assert await survivor.run_once() == 0  # still leased by the dead worker: hands off
    await asyncio.sleep(0.3)  # the dead worker's lease expires
    await survivor.run_once()
    d = await doc(api.as_("ana"), shared_trip, doc_id)
    assert d["status"] == "complete"
    r = await download(api.as_("ana"), shared_trip, doc_id)
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == d["sha256"]


async def test_snapshot_is_taken_at_request_time_not_at_generation_time(
    api, shared_trip, mock_control, make_worker
):
    r = await request_doc(api.as_("ana"), shared_trip)
    # The trip changes before the worker gets to it.
    await api.as_("ana").patch(f"/trips/{shared_trip['id']}",
                               json={"version": 1, "name": "Renamed later"})
    await add_item(api.as_("ana"), shared_trip, HOTEL)
    await make_worker().run_once()
    html = (await download(api.as_("ana"), shared_trip, r.json()["documentId"])).text
    assert "Iberia + Paris" in html and "Renamed later" not in html
    assert "Trip version 1" in html and "No options in the itinerary" in html


# --- US-10: later edits never alter an existing document -------------------------------------


async def test_later_edits_do_not_alter_an_existing_document(
    api, shared_trip, mock_control, make_worker
):
    ana = api.as_("ana")
    item = await add_item(ana, shared_trip, HOTEL)
    old = await generated(api, make_worker, shared_trip)
    before = (await download(ana, shared_trip, old["documentId"])).content

    await ana.patch(f"/trips/{shared_trip['id']}", json={"version": 2, "name": "New name"})
    await book(ana, shared_trip, item)
    await add_item(ana, shared_trip, SURF)
    await make_worker().run_once()

    assert (await download(ana, shared_trip, old["documentId"])).content == before
    assert (await doc(ana, shared_trip, old["documentId"])) == old
    new = await generated(api, make_worker, shared_trip)
    assert new["documentId"] != old["documentId"] and new["tripVersion"] == 4
    html = (await download(ana, shared_trip, new["documentId"])).text
    assert "New name" in html and "confirmed (booked)" in html


async def test_the_database_keeps_snapshots_immutable(api, app, shared_trip, mock_control,
                                                      make_worker):
    d = await generated(api, make_worker, shared_trip)
    for change in ("snapshot = '{}'::jsonb", "trip_version = 99", "snapshot_at = now()"):
        async with SqlUnitOfWork(app.state.sessions) as uow:
            with pytest.raises(DBAPIError, match="immutable"):
                await uow.session.execute(text(f"UPDATE documents SET {change} WHERE id = :id"),
                                          {"id": d["documentId"]})


# --- Stored files that do not match their references ------------------------------------------


@pytest.mark.parametrize("damage", ["deleted", "corrupted", "truncated"])
async def test_damaged_file_is_never_served_and_is_regenerated_from_the_snapshot(
    api, shared_trip, mock_control, make_worker, storage_dir, damage
):
    await add_item(api.as_("ana"), shared_trip, HOTEL)
    d = await generated(api, make_worker, shared_trip)
    path = storage_dir / str(shared_trip["id"]) / f"{d['documentId']}.html"
    original = path.read_bytes()
    if damage == "deleted":
        path.unlink()
    elif damage == "corrupted":
        path.write_bytes(original.replace(b"670.00", b"070.00"))  # same size, other bytes
    else:
        path.write_bytes(original[:100])

    r = await download(api.as_("carla"), shared_trip, d["documentId"])
    assert r.status_code == 409 and r.json()["code"] == "DOCUMENT_FILE_UNAVAILABLE"
    assert r.json()["details"]["problem"] == ("FILE_MISSING" if damage == "deleted"
                                              else "FILE_CORRUPT")
    assert b"070.00" not in r.content
    marked = await doc(api.as_("carla"), shared_trip, d["documentId"])
    assert marked["status"] == "waiting" and "regenerating" in marked["error"]

    await make_worker().run_once()
    restored = await doc(api.as_("carla"), shared_trip, d["documentId"])
    assert restored["status"] == "complete" and restored["sha256"] == d["sha256"]
    assert (await download(api.as_("carla"), shared_trip, d["documentId"])).content == original


async def test_storage_sweep_removes_orphans_and_requeues_missing_files(
    api, shared_trip, mock_control, make_worker, storage_dir
):
    d = await generated(api, make_worker, shared_trip)
    path = storage_dir / str(shared_trip["id"]) / f"{d['documentId']}.html"
    orphan = storage_dir / "00000000-0000-0000-0000-000000000000" / "lost.html"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("orphan")
    stale_temp = path.with_name(path.name + ".tmp-old")
    stale_temp.write_text("partial")
    os.utime(stale_temp, (time.time() - 3600, time.time() - 3600))
    path.unlink()  # a referenced file disappears

    worker = make_worker()
    await worker.sweep_documents()
    assert not orphan.exists() and not stale_temp.exists()
    assert (await doc(api.as_("ana"), shared_trip, d["documentId"]))["status"] == "waiting"
    await worker.run_once()
    assert path.exists()
    assert (await doc(api.as_("ana"), shared_trip, d["documentId"]))["status"] == "complete"


async def test_rendering_is_deterministic():
    snapshot = {"takenAt": "2030-06-01T12:00:00+00:00", "trip": {
        "id": "t", "version": 1, "name": "<T>", "destinations": ["Lisbon"],
        "startsOn": "2030-06-10", "endsOn": "2030-06-14", "travelers": 2,
        "budget": {"amount": "1.00", "currency": "USD"}}, "itinerary": [], "missing": [],
        "rules": [], "bookings": [], "costs": {
            "lines": [], "estimatedTotal": None, "confirmedTotal": None, "total": None,
            "budget": None, "remaining": None, "overBudget": False, "usedPercent": None,
            "rounding": "r"}}
    renderer = HtmlDocumentRenderer()
    assert renderer.render(snapshot) == renderer.render(dict(snapshot))
    assert b"&lt;T&gt;" in renderer.render(snapshot)  # user text is escaped


# --- Permissions (closes US-02 for documents) -----------------------------------------------


async def test_participants_download_and_nonparticipants_get_404(
    api, shared_trip, trip_payload, mock_control, make_worker
):
    d = await generated(api, make_worker, shared_trip)
    for user in ("ana", "beto", "carla"):
        assert (await download(api.as_(user), shared_trip, d["documentId"])).status_code == 200
    dani, base = api.as_("dani"), f"/trips/{shared_trip['id']}/documents"
    for r in (await dani.get(base), await dani.post(base),
              await dani.get(f"{base}/{d['documentId']}"),
              await download(dani, shared_trip, d["documentId"]),
              await dani.post(f"{base}/{d['documentId']}/retry")):
        assert r.status_code == 404 and r.json()["code"] == "TRIP_NOT_FOUND"
    assert (await download(api.as_(None), shared_trip, d["documentId"])).status_code == 401

    # A document id of another trip is not reachable through this trip.
    other = (await api.as_("dani").post("/trips", json=trip_payload)).json()
    r = await download(api.as_("dani"), other, d["documentId"])
    assert r.status_code == 404 and r.json()["code"] == "DOCUMENT_NOT_FOUND"


async def test_viewers_cannot_start_document_generation(api, shared_trip):
    r = await request_doc(api.as_("carla"), shared_trip)
    assert r.status_code == 403
    assert (await api.as_("carla").get(f"/trips/{shared_trip['id']}/documents")).json() == []


async def test_removed_participant_loses_access_to_documents(
    api, shared_trip, mock_control, make_worker
):
    d = await generated(api, make_worker, shared_trip)
    await api.as_("ana").delete(f"/trips/{shared_trip['id']}/members/carla")
    r = await download(api.as_("carla"), shared_trip, d["documentId"])
    assert r.status_code == 404
