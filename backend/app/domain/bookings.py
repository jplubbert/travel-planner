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
# At most one of these per itinerary item (enforced by a partial unique index).
ACTIVE = frozenset({BookingStatus.DRAFT, BookingStatus.SUBMITTED, BookingStatus.UNKNOWN,
                    BookingStatus.PENDING, BookingStatus.CONFIRMED})

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
    # Last problem talking to the provider: {code, message, providerRequestId,
    # retryAfterSeconds, at}. Kept for diagnosis and shown to the user (ADR 0003 rule 8).
    last_error: dict[str, Any] | None = None
    # For a rejection because terms changed: the provider's current offer at that moment.
    rejection_terms: dict[str, Any] | None = None
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

    def record_rejection(self, code: str, message: str, now: datetime,
                         terms: dict[str, Any] | None = None) -> None:
        self._move(S.REJECTED, now)
        self.failure_code, self.failure_message = code, message
        self.rejection_terms = terms

    def record_not_accepted(self, error: dict[str, Any], now: datetime) -> None:
        """The provider definitely did not accept the request (e.g. 429/503)."""
        self._move(S.DRAFT, now)
        self.last_error = error

    def record_unknown(self, error: dict[str, Any], now: datetime) -> None:
        self._move(S.UNKNOWN, now)
        self.last_error = error

    def note_error(self, error: dict[str, Any], now: datetime) -> None:
        """A failed attempt to learn the outcome; the state does not change."""
        self.last_error = error
        self.updated_at = now


def next_step(booking: Booking) -> str:
    """What the user can do now, in plain words (US-08)."""
    error = booking.last_error or {}
    match booking.status:
        case S.DRAFT:
            wait = (f" Wait at least {error['retryAfterSeconds']} seconds before retrying."
                    if error.get("retryAfterSeconds") else "")
            return ("Not booked: the provider did not accept the request "
                    f"({error.get('code', 'unknown reason')}).{wait} You can retry; the retry "
                    "reuses the same booking request, so it cannot create a duplicate.")
        case S.SUBMITTED:
            return "Being sent to the provider. Reload in a moment to see the outcome."
        case S.UNKNOWN:
            return ("Outcome not known yet: the provider's answer was lost, so this may or may "
                    "not be booked. Do not book this option again; the application is "
                    "checking with the provider automatically.")
        case S.PENDING:
            return ("The provider accepted the request and is confirming it. The application "
                    "checks automatically; nothing to do.")
        case S.CONFIRMED:
            return "Booked. The accepted price will not change. Nothing else to do."
        case S.FAILED:
            return ("The provider accepted the request but the booking failed "
                    f"({booking.failure_code}: {booking.failure_message}). Nothing was "
                    "booked. You can start a new booking for this option or choose another.")
        case S.REJECTED:
            terms = booking.rejection_terms
            current = (f" Current price: {terms['price']['amount']} USD"
                       f"{'' if terms['available'] else ' (unavailable)'}."
                       if terms else "")
            return ("Not booked: the provider did not accept these terms "
                    f"({booking.failure_code}).{current} Review the current terms and start "
                    "a new booking if you agree.")
    raise AssertionError(booking.status)
