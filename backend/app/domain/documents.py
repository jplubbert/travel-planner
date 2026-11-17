"""Trip documents (US-09, US-10). A document renders an immutable snapshot of the trip,
its itinerary, costs and booking statuses taken when it was requested (ADR 0005)."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
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
    # in_progress -> waiting: the generation was interrupted and will be redone.
    D.IN_PROGRESS: frozenset({D.COMPLETE, D.FAILED, D.WAITING}),
    # complete -> waiting: the stored file was found missing or corrupt; it is regenerated
    # from the same snapshot. A document is never "complete" without a verified file.
    D.COMPLETE: frozenset({D.WAITING}),
    D.FAILED: frozenset({D.WAITING}),  # retry
}
ACTIVE = frozenset({D.WAITING, D.IN_PROGRESS})


@dataclass
class Document:
    id: UUID
    trip_id: UUID
    trip_version: int
    requested_by: str
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime
    # The information the document represents, frozen at request time.
    snapshot: dict[str, Any]
    snapshot_at: datetime
    snapshot_sha256: str
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

    @property
    def file_key(self) -> str:
        """Where the file lives; deterministic so a regeneration overwrites, never forks."""
        return f"{self.trip_id}/{self.id}.html"


def canonical_sha256(data: Any) -> str:
    """Hash of JSON data independent of key order."""
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
