"""Durable background jobs (document generation, booking reconciliation)."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.errors import InvalidTransition


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


J = JobStatus
ALLOWED: dict[JobStatus, frozenset[JobStatus]] = {
    J.QUEUED: frozenset({J.RUNNING}),
    J.RUNNING: frozenset({J.SUCCEEDED, J.FAILED, J.QUEUED}),  # queued = retry / lease expired
    J.SUCCEEDED: frozenset(),
    J.FAILED: frozenset({J.QUEUED}),  # manual retry
}


@dataclass
class Job:
    id: UUID
    kind: str
    status: JobStatus
    run_after: datetime
    created_at: datetime
    updated_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None
    attempts: int = 0
    max_attempts: int = 5
    locked_until: datetime | None = None
    last_error: str | None = None

    def move(self, to: JobStatus, now: datetime) -> None:
        if to not in ALLOWED[self.status]:
            raise InvalidTransition(f"Job cannot move from {self.status} to {to}.")
        self.status = to
        self.updated_at = now
