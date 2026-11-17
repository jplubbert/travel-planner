"""Status channel (SSE) per trip: change announcements with the same permissions as the
trip, closed when access is lost. Served by a real uvicorn server so streaming is real."""

import asyncio
import json
import socket
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import uvicorn

HOTEL = "offer-lis-hotel-central-p2"


@pytest.fixture
async def server(app) -> AsyncIterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off",
                                        log_level="warning"))
    task = asyncio.create_task(srv.serve())
    while not srv.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = srv.force_exit = True
    await task


class Stream:
    """Reads Server-Sent Events from an open response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.lines = response.aiter_lines()

    async def next(self, within: float = 5.0) -> tuple[str, dict[str, Any]] | None:
        """The next event (name, data); None when the server closed the stream."""
        async def read() -> tuple[str, dict[str, Any]] | None:
            name, data = None, None
            async for line in self.lines:
                if line.startswith("event:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1])
                elif line == "" and name:
                    return name, data or {}
            return None
        return await asyncio.wait_for(read(), within)

    async def wait_for(self, predicate: Any, within: float = 5.0) -> dict[str, Any]:
        async def loop() -> dict[str, Any]:
            while True:
                event = await self.next(within)
                assert event is not None, "stream closed"
                if predicate(*event):
                    return event[1]
        return await asyncio.wait_for(loop(), within)


async def open_stream(base_url: str, trip: dict, user: str | None):
    client = httpx.AsyncClient(base_url=base_url, timeout=10,
                               headers={"X-User-Id": user} if user else {})
    response = await client.send(client.build_request("GET", f"/trips/{trip['id']}/events"),
                                 stream=True)
    return client, response


async def test_participants_get_change_notices_for_documents_bookings_and_trip(
    server, api, shared_trip, mock_control, make_worker
):
    client, response = await open_stream(server, shared_trip, "carla")  # a viewer
    try:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        stream = Stream(response)
        name, data = await stream.next()
        assert name == "ready" and "GET" in data["hint"]

        ana = api.as_("ana")
        r = await ana.post(f"/trips/{shared_trip['id']}/itinerary/items",
                           json={"version": 1, "offerId": HOTEL})
        item = r.json()["items"][0]
        assert (await stream.wait_for(lambda n, d: n == "trip"))["version"] == 2

        doc_id = (await ana.post(f"/trips/{shared_trip['id']}/documents")).json()["documentId"]
        notice = await stream.wait_for(lambda n, d: n == "document")
        assert notice == {"id": doc_id, "status": "waiting"}
        await make_worker().run_once()
        await stream.wait_for(lambda n, d: n == "document" and d["status"] == "complete")

        booking = (await ana.post(
            f"/trips/{shared_trip['id']}/itinerary/items/{item['itemId']}/bookings",
            json={"expectedRevision": item["quote"]["revision"],
                  "expectedPrice": item["quote"]["price"]})).json()
        await stream.wait_for(lambda n, d: n == "booking" and d["id"] == booking["bookingId"]
                              and d["status"] == "confirmed")
    finally:
        await response.aclose()
        await client.aclose()


async def test_events_are_only_hints_and_state_is_recovered_with_get(
    server, api, shared_trip, mock_control, make_worker
):
    client, response = await open_stream(server, shared_trip, "beto")
    await Stream(response).next()
    await response.aclose()  # disconnected
    # Changes happen while nobody listens...
    doc_id = (await api.as_("ana").post(f"/trips/{shared_trip['id']}/documents")).json()[
        "documentId"]
    await make_worker().run_once()
    # ...a reconnect does not replay them: it says "ready", and GET has the truth.
    response = await client.send(
        client.build_request("GET", f"/trips/{shared_trip['id']}/events"), stream=True)
    try:
        stream = Stream(response)
        assert (await stream.next())[0] == "ready"
        state = await api.as_("beto").get(f"/trips/{shared_trip['id']}/documents/{doc_id}")
        assert state.json()["status"] == "complete"
    finally:
        await response.aclose()
        await client.aclose()


async def test_stream_closes_when_the_participant_loses_access(server, api, shared_trip):
    client, response = await open_stream(server, shared_trip, "carla")
    try:
        stream = Stream(response)
        assert (await stream.next())[0] == "ready"
        await api.as_("ana").delete(f"/trips/{shared_trip['id']}/members/carla")
        name, data = await stream.next()
        assert name == "access-revoked" and data == {"tripId": shared_trip["id"]}
        assert await stream.next() is None  # the server ended the stream
    finally:
        await response.aclose()
        await client.aclose()


@pytest.mark.parametrize("user,status", [("dani", 404), (None, 401)])
async def test_nonparticipants_cannot_subscribe(server, shared_trip, user, status):
    client, response = await open_stream(server, shared_trip, user)
    try:
        assert response.status_code == status
        await response.aread()
        assert response.json()["code"] in ("TRIP_NOT_FOUND", "UNAUTHENTICATED")
    finally:
        await response.aclose()
        await client.aclose()


async def test_nonparticipant_subscription_to_a_missing_trip_looks_the_same(server, api):
    client, response = await open_stream(
        server, {"id": "00000000-0000-0000-0000-000000000000"}, "dani")
    try:
        assert response.status_code == 404
    finally:
        await response.aclose()
        await client.aclose()
