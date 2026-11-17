"""What the application knows about the external travel provider, independent of HTTP.

Errors are split by what they mean for the caller, not by status code alone. In particular
``delivery_uncertain`` tells booking logic (ADR 0003) whether the provider may have
processed a request whose answer we did not get.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from app.domain.errors import DomainError

Category = Literal["transport", "accommodation", "activity"]
CATEGORIES: tuple[Category, ...] = ("transport", "accommodation", "activity")
ProviderBookingStatus = Literal["pending", "confirmed", "failed"]


@dataclass(frozen=True)
class Offer:
    """A provider quote. The price covers the whole party and the whole option; it is an
    estimate until a booking confirms it, and only valid until ``valid_until``."""

    id: str
    provider_id: str
    category: Category
    title: str
    description: str
    destination: str
    origin: str | None
    starts_at: datetime
    ends_at: datetime
    travelers: int
    price: Decimal
    currency: str
    revision: str
    available: bool
    valid_until: datetime
    restrictions: tuple[str, ...]

    def quote_expired(self, now: datetime) -> bool:
        return now >= self.valid_until


@dataclass(frozen=True)
class SupplierWarning:
    provider_id: str
    code: str  # PROVIDER_UNAVAILABLE | PROVIDER_TIMEOUT | RATE_LIMITED
    message: str


@dataclass(frozen=True)
class SearchQuery:
    category: Category
    destination: str
    starts_on: date
    ends_on: date
    travelers: int
    origin: str | None = None
    provider_id: str | None = None


@dataclass(frozen=True)
class SearchResult:
    offers: tuple[Offer, ...]
    partial: bool
    warnings: tuple[SupplierWarning, ...]
    searched_at: datetime


@dataclass(frozen=True)
class BookingRequest:
    client_reference: str
    offer_id: str
    expected_revision: str
    expected_price: Decimal


@dataclass(frozen=True)
class ProviderBooking:
    id: str
    client_reference: str
    offer: Offer
    status: ProviderBookingStatus
    created_at: datetime
    updated_at: datetime
    failure: dict[str, str] | None
    replayed: bool = False  # True when an idempotent retry returned an existing booking


# --- Errors ------------------------------------------------------------------


class ProviderError(DomainError):
    code = "PROVIDER_ERROR"
    # True when the provider may have processed the request even though we got no usable
    # answer (timeout, broken connection, unexpected response). False when the contract
    # guarantees nothing was done (400/401/404/409/429/503 with an Error body, or no
    # connection was ever made).
    delivery_uncertain = False

    def __init__(
        self, message: str, *, provider_code: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_code = provider_code
        self.provider_request_id = provider_request_id


class ProviderBadRequest(ProviderError):
    code = "PROVIDER_BAD_REQUEST"


class ProviderAuthFailed(ProviderError):
    code = "PROVIDER_AUTH_FAILED"


class ProviderNotFound(ProviderError):
    code = "PROVIDER_NOT_FOUND"


class ProviderConflict(ProviderError):
    code = "PROVIDER_CONFLICT"


class OfferChanged(ProviderConflict):
    code = "OFFER_CHANGED"


class OfferUnavailable(ProviderConflict):
    code = "OFFER_UNAVAILABLE"


class IdempotencyConflict(ProviderConflict):
    code = "IDEMPOTENCY_CONFLICT"


class ReferenceConflict(ProviderConflict):
    code = "REFERENCE_CONFLICT"


class ProviderRateLimited(ProviderError):
    code = "PROVIDER_RATE_LIMITED"

    def __init__(self, message: str, *, retry_after_seconds: int | None = None,
                 provider_request_id: str | None = None) -> None:
        super().__init__(message, provider_code="RATE_LIMITED",
                         provider_request_id=provider_request_id)
        self.retry_after_seconds = retry_after_seconds


class ProviderUnavailable(ProviderError):
    code = "PROVIDER_UNAVAILABLE"

    def __init__(self, message: str, *, warnings: tuple[SupplierWarning, ...] = (),
                 provider_request_id: str | None = None) -> None:
        super().__init__(message, provider_code="PROVIDER_UNAVAILABLE",
                         provider_request_id=provider_request_id)
        self.warnings = warnings


class ProviderUnreachable(ProviderError):
    """No connection could be established: the request never reached the provider."""

    code = "PROVIDER_UNREACHABLE"


class ProviderTimeout(ProviderError):
    code = "PROVIDER_TIMEOUT"
    delivery_uncertain = True


class ProviderTransportError(ProviderError):
    """The connection broke after the request may have been sent."""

    code = "PROVIDER_TRANSPORT_ERROR"
    delivery_uncertain = True


class ProviderProtocolError(ProviderError):
    """Unexpected status or a body that does not match the contract."""

    code = "PROVIDER_BAD_RESPONSE"
    delivery_uncertain = True
