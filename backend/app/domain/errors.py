from dataclasses import dataclass
from typing import Any


class DomainError(Exception):
    code = "DOMAIN_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


class ValidationFailed(DomainError):
    code = "VALIDATION_ERROR"

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("Some fields are invalid; see details.")
        self.errors = errors


class NotFound(DomainError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Forbidden(DomainError):
    code = "FORBIDDEN"


class Conflict(DomainError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VersionConflict(DomainError):
    """ADR 0001: the write was based on a stale version. Carries what is saved now
    (trip and itinerary view) and the changes accepted since the caller's version."""

    code = "TRIP_VERSION_CONFLICT"

    def __init__(self, current: Any, your_version: int, changes: Any, itinerary: Any) -> None:
        super().__init__(
            f"The trip changed since version {your_version}, which your change was based "
            "on. Nothing was saved. Review the current state and the changes listed, then "
            "reapply your change with the current version."
        )
        self.current = current
        self.your_version = your_version
        self.changes = changes
        self.itinerary = itinerary


class VersionRequired(DomainError):
    code = "VERSION_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            "Send the 'version' of the trip you loaded so concurrent edits are not lost."
        )


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"


def trip_not_found() -> NotFound:
    # Same answer for "does not exist" and "you are not a participant" (no existence leak).
    return NotFound("TRIP_NOT_FOUND", "Trip not found.")
