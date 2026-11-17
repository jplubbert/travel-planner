"""US-03: search provider offers for a trip and classify the outcome."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.domain.access import Role, TripAccess
from app.domain.errors import FieldError, ValidationFailed
from app.domain.provider import (
    CATEGORIES,
    Offer,
    ProviderBadRequest,
    ProviderError,
    ProviderUnavailable,
    SearchQuery,
    SupplierWarning,
)
from app.domain.trips import get_trip

if TYPE_CHECKING:
    from app.ports.clock import Clock
    from app.ports.provider import ProviderClient
    from app.ports.repositories import UnitOfWork


class SearchOutcomeKind(StrEnum):
    COMPLETE = "complete"  # every queried supplier answered and there are offers
    PARTIAL = "partial"  # some suppliers failed; the others' offers are still usable
    EMPTY = "empty"  # every queried supplier answered; nothing matches
    FAILED = "failed"  # no usable answer from the provider


@dataclass(frozen=True)
class SearchInput:
    category: Any
    destination: Any
    origin: Any = None
    starts_on: date | None = None
    ends_on: date | None = None
    provider_id: Any = None


@dataclass(frozen=True)
class SearchOutcome:
    kind: SearchOutcomeKind
    query: SearchQuery
    offers: tuple[Offer, ...]
    failed_suppliers: tuple[SupplierWarning, ...]
    searched_at: datetime | None  # provider's time; None when the search failed
    received_at: datetime
    error: ProviderError | None = None


def _query_for(data: SearchInput, trip_starts: date, trip_ends: date,
               travelers: int) -> SearchQuery:
    errors: list[FieldError] = []
    if data.category not in CATEGORIES:
        errors.append(FieldError("category", f"must be one of {', '.join(CATEGORIES)}"))
    destination = data.destination.strip() if isinstance(data.destination, str) else ""
    if not destination or len(destination) > 200:
        errors.append(FieldError("destination", "is required (1-200 characters)"))
    origin = data.origin.strip() if isinstance(data.origin, str) else None
    if data.category == "transport" and not origin:
        errors.append(FieldError("origin", "is required for transport searches"))
    if origin is not None and len(origin) > 200:
        errors.append(FieldError("origin", "must be at most 200 characters"))
    provider_id = data.provider_id.strip() if isinstance(data.provider_id, str) else None
    if provider_id is not None and not 1 <= len(provider_id) <= 128:
        errors.append(FieldError("providerId", "must be 1-128 characters"))

    starts_on, ends_on = data.starts_on or trip_starts, data.ends_on or trip_ends
    if starts_on > ends_on:
        errors.append(FieldError("endsOn", "must be on or after startsOn"))
    if starts_on < trip_starts or ends_on > trip_ends:
        errors.append(FieldError(
            "startsOn" if starts_on < trip_starts else "endsOn",
            f"must be within the trip dates ({trip_starts} to {trip_ends})"))
    if errors:
        raise ValidationFailed(errors)
    return SearchQuery(
        category=data.category, destination=destination, starts_on=starts_on, ends_on=ends_on,
        travelers=travelers, origin=origin if data.category == "transport" else None,
        provider_id=provider_id,
    )


async def search_offers(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", access: TripAccess,
    data: SearchInput,
) -> SearchOutcome:
    """Any participant may search (US-03 is a viewer capability). The party size always
    comes from the trip so offers fit it; dates default to the trip's and must lie
    within them."""
    access.require(Role.VIEWER)
    trip = await get_trip(uow, access)
    query = _query_for(data, trip.starts_on, trip.ends_on, trip.travelers)
    # Do not hold a database connection while waiting on the network.
    await uow.rollback()

    try:
        result = await provider.search(query)
    except ProviderBadRequest as exc:
        raise ValidationFailed([FieldError(
            "query", f"The provider rejected this search: {exc.message}")]) from exc
    except ProviderError as exc:
        failed = exc.warnings if isinstance(exc, ProviderUnavailable) else ()
        return SearchOutcome(SearchOutcomeKind.FAILED, query, (), failed, None, clock.now(), exc)

    if result.partial:
        kind = SearchOutcomeKind.PARTIAL
    elif result.offers:
        kind = SearchOutcomeKind.COMPLETE
    else:
        kind = SearchOutcomeKind.EMPTY
    return SearchOutcome(kind, query, result.offers, result.warnings, result.searched_at,
                         clock.now())
