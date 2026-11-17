"""Local booking states and their transitions (ADR 0003)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.errors import InvalidTransition


class BookingStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNKNOWN = "unknown"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"  # accepted by the provider, then failed
    REJECTED = "rejected"  # never accepted by the provider

    @property
    def terminal(self) -> bool:
        return self in TERMINAL


TERMINAL = frozenset({BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.REJECTED})

S = BookingStatus
ALLOWED: dict[BookingStatus, frozenset[BookingStatus]] = {
    S.DRAFT: frozenset({S.SUBMITTED}),
    # 429/503 with a contract Error body means "not accepted": back to draft, safe to retry.
    S.SUBMITTED: frozenset({S.PENDING, S.CONFIRMED, S.FAILED, S.REJECTED, S.UNKNOWN, S.DRAFT}),
    # Only reconciliation leaves unknown, and only towards what the provider reports.
    S.UNKNOWN: frozenset({S.PENDING, S.CONFIRMED, S.FAILED, S.REJECTED}),
    S.PENDING: frozenset({S.CONFIRMED, S.FAILED}),
    S.CONFIRMED: frozenset(),
    S.FAILED: frozenset(),
    S.REJECTED: frozenset(),
}

PROVIDER_STATUSES = {"pending": S.PENDING, "confirmed": S.CONFIRMED, "failed": S.FAILED}


@dataclass
class Booking:
    id: UUID
    trip_id: UUID
    itinerary_item_id: UUID | None
    offer_id: str
    expected_revision: str
    expected_price: Decimal
    client_reference: str
    idempotency_key: str
    requested_by: str
    status: BookingStatus
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    provider_booking_id: str | None = None
    accepted_offer: dict[str, Any] | None = None
    accepted_price: Decimal | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    last_error: str | None = None
    version: int = 1

    def _move(self, to: BookingStatus, now: datetime) -> None:
        if to not in ALLOWED[self.status]:
            raise InvalidTransition(f"Booking cannot move from {self.status} to {to}.")
        self.status = to
        self.updated_at = now

    def submit(self, now: datetime) -> None:
        self._move(S.SUBMITTED, now)
        self.submitted_at = now
        self.last_error = None

    def record_provider_booking(
        self, *, provider_booking_id: str, provider_status: str, offer: dict[str, Any],
        price: Decimal, failure: dict[str, str] | None, now: datetime,
    ) -> None:
        """The provider accepted the request (directly, by replay, or found by reference)."""
        target = PROVIDER_STATUSES[provider_status]
        if self.provider_booking_id is not None and self.provider_booking_id != provider_booking_id:
            raise InvalidTransition("Provider returned a different booking for this intent.")
        if self.status == target and self.status == S.PENDING:
            return  # still pending: nothing new
        self._move(target, now)
        if self.accepted_offer is None:  # accepted snapshot is immutable
            self.provider_booking_id = provider_booking_id
            self.accepted_offer = offer
            self.accepted_price = price
        if target == S.FAILED and failure:
            self.failure_code, self.failure_message = failure["code"], failure["message"]

    def record_rejection(self, code: str, message: str, now: datetime) -> None:
        self._move(S.REJECTED, now)
        self.failure_code, self.failure_message = code, message

    def record_not_accepted(self, reason: str, now: datetime) -> None:
        """Provider answered 429/503: the request was definitely not accepted."""
        self._move(S.DRAFT, now)
        self.last_error = reason

    def record_unknown(self, reason: str, now: datetime) -> None:
        self._move(S.UNKNOWN, now)
        self.last_error = reason
