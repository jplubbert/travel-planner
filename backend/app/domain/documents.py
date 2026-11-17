"""Trip documents (US-09, US-10). A document is a snapshot of one trip version."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.errors import InvalidTransition


class DocumentStatus(StrEnum):
    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"


D = DocumentStatus
ALLOWED: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    D.WAITING: frozenset({D.IN_PROGRESS, D.FAILED}),
    # in_progress -> waiting: a worker died mid-generation and the job was requeued.
    D.IN_PROGRESS: frozenset({D.COMPLETE, D.FAILED, D.WAITING}),
    D.COMPLETE: frozenset(),
    D.FAILED: frozenset({D.WAITING}),  # retry
}


@dataclass
class Document:
    id: UUID
    trip_id: UUID
    trip_version: int
    requested_by: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    storage_key: str | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None
    error: str | None = None
    completed_at: datetime | None = None

    def move(self, to: DocumentStatus, now: datetime) -> None:
        if to not in ALLOWED[self.status]:
            raise InvalidTransition(f"Document cannot move from {self.status} to {to}.")
        self.status = to
        self.updated_at = now
