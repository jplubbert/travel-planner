"""US-09 / US-10 documents and the per-trip status channel (SSE)."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.adapters.sqlalchemy.uow import SqlUnitOfWork
from app.api.deps import ClockDep, Editor, UoW, Viewer
from app.domain import document_service, status_feed
from app.domain.documents import Document, DocumentStatus
from app.domain.errors import DomainError

router = APIRouter()
logger = logging.getLogger("app.events")


class DocumentOut(BaseModel):
    documentId: UUID
    tripId: UUID
    tripVersion: int
    snapshotAt: datetime
    status: DocumentStatus
    ready: bool
    requestedBy: str
    createdAt: datetime
    updatedAt: datetime
    completedAt: datetime | None
    sizeBytes: int | None
    sha256: str | None
    error: str | None
    downloadUrl: str | None


def document_out(d: Document) -> DocumentOut:
    ready = d.status == DocumentStatus.COMPLETE
    return DocumentOut(
        documentId=d.id, tripId=d.trip_id, tripVersion=d.trip_version,
        snapshotAt=d.snapshot_at, status=d.status, ready=ready, requestedBy=d.requested_by,
        createdAt=d.created_at, updatedAt=d.updated_at, completedAt=d.completed_at,
        sizeBytes=d.size_bytes, sha256=d.content_sha256, error=d.error,
        downloadUrl=f"/trips/{d.trip_id}/documents/{d.id}/download" if ready else None,
    )


@router.post("/trips/{trip_id}/documents", status_code=202,
             responses={200: {"model": DocumentOut}})
async def request_document(access: Editor, uow: UoW, clock: ClockDep,
                           request: Request) -> JSONResponse:
    """202 with a new document being generated, or the equivalent existing document
    (202 if it is still being generated for this version, 200 if already complete)."""
    result = await document_service.request_document(uow, clock, access,
                                                      request.app.state.settings
                                                      .document_max_attempts)
    d = result.document
    status = 200 if d.status == DocumentStatus.COMPLETE else 202
    return JSONResponse(document_out(d).model_dump(mode="json"), status_code=status,
                        headers={"Location": f"/trips/{d.trip_id}/documents/{d.id}"})


@router.get("/trips/{trip_id}/documents")
async def list_documents(access: Viewer, uow: UoW) -> list[DocumentOut]:
    return [document_out(d) for d in await document_service.list_documents(uow, access)]


@router.get("/trips/{trip_id}/documents/{document_id}")
async def get_document(document_id: str, access: Viewer, uow: UoW) -> DocumentOut:
    return document_out(await document_service.get_document(uow, access, document_id))


@router.post("/trips/{trip_id}/documents/{document_id}/retry", status_code=202)
async def retry_document(document_id: str, access: Editor, uow: UoW, clock: ClockDep,
                         request: Request) -> DocumentOut:
    return document_out(await document_service.retry_document(
        uow, clock, access, document_id, request.app.state.settings.document_max_attempts))


@router.get("/trips/{trip_id}/documents/{document_id}/download")
async def download_document(document_id: str, access: Viewer, uow: UoW, clock: ClockDep,
                            request: Request) -> Response:
    state = request.app.state
    result = await document_service.download_document(
        uow, state.storage, clock, access, document_id, state.settings.document_max_attempts)
    d = result.document
    return Response(
        content=result.content, media_type=state.renderer.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="trip-{d.trip_id}-v{d.trip_version}'
                                   f'-{d.id}.html"',
            "ETag": f'"{d.content_sha256}"',
            "X-Content-SHA256": d.content_sha256 or "",
            "X-Trip-Version": str(d.trip_version),
            "X-Snapshot-At": d.snapshot_at.isoformat(),
            "Cache-Control": "private, no-store",
        },
    )


# --- Status channel -------------------------------------------------------------------------


def _event(name: str, data: dict[str, Any], event_id: int | None = None) -> str:
    lines = [f"event: {name}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@router.get("/trips/{trip_id}/events", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}}})
async def trip_events(access: Viewer, request: Request) -> StreamingResponse:
    """Server-Sent Events announcing booking, document and trip changes. Events are hints:
    after (re)connecting, clients must GET the state they show. Access is re-checked on
    every poll; the stream ends with ``access-revoked`` if the user loses access."""
    state = request.app.state
    poll = state.settings.events_poll_seconds
    heartbeat = state.settings.events_heartbeat_seconds

    async def stream() -> AsyncIterator[str]:
        event_id = 0
        try:
            async with SqlUnitOfWork(state.sessions) as uow:
                last = await status_feed.digest(uow, access)
        except DomainError:
            yield _event("access-revoked", {"tripId": str(access.trip_id)})
            return
        yield "retry: 3000\n\n"
        yield _event("ready", {"tripId": str(access.trip_id), "hint":
                               "Load the current state with GET; events only announce "
                               "changes."})
        last_sent = time.monotonic()
        while not await request.is_disconnected():
            await asyncio.sleep(poll)
            try:
                async with SqlUnitOfWork(state.sessions) as uow:
                    current = await status_feed.digest(uow, access)
            except DomainError:
                yield _event("access-revoked", {"tripId": str(access.trip_id)})
                logger.info("events: closing stream for %s on trip %s (access lost)",
                            access.user_id, access.trip_id)
                return
            for change in status_feed.changes(last, current):
                event_id += 1
                payload: dict[str, Any] = {"id": change.id}
                if change.status is not None:
                    payload["status"] = change.status
                if change.version is not None:
                    payload["version"] = change.version
                yield _event(change.kind, payload, event_id)
                last_sent = time.monotonic()
            last = current
            if time.monotonic() - last_sent >= heartbeat:
                yield ": keep-alive\n\n"
                last_sent = time.monotonic()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

