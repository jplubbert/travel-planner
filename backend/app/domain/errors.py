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
    """ADR 0001: the write was based on a stale version; ``current`` is what is saved."""

    code = "TRIP_VERSION_CONFLICT"

    def __init__(self, current: Any) -> None:
        super().__init__(
            "The trip was changed by someone else since you loaded it. "
            "Review the current version and reapply your change."
        )
        self.current = current


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
