"""Itinerary items (US-04) and the checks shown when reading the itinerary.

Scheduling rules are deliberately simple and are returned to clients verbatim (RULES).
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from app.domain.bookings import Booking, BookingStatus
from app.domain.provider import Offer

if TYPE_CHECKING:
    from app.domain.trips import Trip

Category = Literal["transport", "accommodation", "activity"]

RULES: tuple[str, ...] = (
    "Times are compared as absolute instants, using each offer's own UTC offset.",
    "Two transport or activity items conflict when their time ranges overlap; one ending "
    "exactly when the next starts is not a conflict.",
    "Accommodation only conflicts with other accommodation (one place per night); "
    "activities and transport during a stay are expected.",
    "Travel time between items, transfers, check-in windows and opening hours are not "
    "considered.",
    "An item is outside the trip when its local start date is before the trip's first day "
    "or its local end date is after the trip's last day.",
    "Every night from the first day to the night before the last day should be covered by "
    "an accommodation; uncovered nights and destinations without accommodation are "
    "reported as missing.",
    "With two or more destinations, they are visited in the order listed; each pair of "
    "consecutive destinations needs a transport item from the first to the second "
    "(matching its origin and destination, in that direction), otherwise the transport is "
    "reported as missing. Travel to the first destination and back home is not checked.",
    "A price is flagged expired once its quote's validUntil has passed; refresh the item to "
    "get current terms. Items with a confirmed booking keep the accepted price.",
    "Availability and price reflect the last quote (quotedAt); they may have changed since.",
)

ACTIVE_BOOKING = frozenset({BookingStatus.SUBMITTED, BookingStatus.UNKNOWN,
                            BookingStatus.PENDING, BookingStatus.CONFIRMED})


@dataclass(frozen=True)
class ItineraryItem:
    """An option selected into the itinerary with a copy of the quote it was added or last
    refreshed with (ADR 0004). ``offer_snapshot`` is that quote in contract format."""

    id: UUID
    trip_id: UUID
    position: int
    offer_id: str
    provider_id: str
    category: Category
    title: str
    description: str
    destination: str
    origin: str | None
    starts_at: datetime  # aware, with the provider's local offset
    ends_at: datetime
    travelers: int
    price: Decimal  # USD, whole option for the whole party; an estimate until booked
    offer_revision: str
    available: bool
    valid_until: datetime
    quoted_at: datetime
    added_by: str
    created_at: datetime
    offer_snapshot: dict[str, Any]
    restrictions: tuple[str, ...] = ()

    @classmethod
    def from_offer(
        cls, offer: Offer, *, id: UUID, trip_id: UUID, position: int, added_by: str,
        now: datetime,
    ) -> "ItineraryItem":
        return cls(
            id=id, trip_id=trip_id, position=position, offer_id=offer.id,
            provider_id=offer.provider_id, category=offer.category, title=offer.title,
            description=offer.description, destination=offer.destination,
            origin=offer.origin, starts_at=offer.starts_at, ends_at=offer.ends_at,
            travelers=offer.travelers, price=offer.price, offer_revision=offer.revision,
            available=offer.available, valid_until=offer.valid_until, quoted_at=now,
            added_by=added_by, created_at=now, offer_snapshot=offer_snapshot(offer),
            restrictions=offer.restrictions,
        )

    def with_quote(self, offer: Offer, now: datetime) -> "ItineraryItem":
        fresh = ItineraryItem.from_offer(offer, id=self.id, trip_id=self.trip_id,
                                         position=self.position, added_by=self.added_by,
                                         now=now)
        return replace(fresh, created_at=self.created_at)

    def quote_differs(self, offer: Offer) -> bool:
        return offer_snapshot(offer) != self.offer_snapshot


def offer_snapshot(offer: Offer) -> dict[str, Any]:
    """The offer in the provider contract's format (exact strings, original offsets)."""
    snapshot: dict[str, Any] = {
        "id": offer.id, "providerId": offer.provider_id, "category": offer.category,
        "title": offer.title, "description": offer.description,
        "destination": offer.destination, "startsAt": offer.starts_at.isoformat(),
        "endsAt": offer.ends_at.isoformat(), "travelers": offer.travelers,
        "price": {"amount": f"{offer.price:.2f}", "currency": offer.currency},
        "revision": offer.revision, "available": offer.available,
        "validUntil": offer.valid_until.isoformat(),
        "restrictions": list(offer.restrictions),
    }
    if offer.origin is not None:
        snapshot["origin"] = offer.origin
    return snapshot


# --- Read-time checks ---------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    # PRICE_EXPIRED | OFFER_UNAVAILABLE | OUTSIDE_TRIP_DATES | SCHEDULE_CONFLICT |
    # PARTY_SIZE_MISMATCH
    code: str
    message: str
    related_item_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Gap:
    # EMPTY_ITINERARY | NO_ACCOMMODATION_FOR_DESTINATION | NIGHTS_WITHOUT_ACCOMMODATION |
    # MISSING_TRANSPORT
    code: str
    message: str


@dataclass(frozen=True)
class ItemView:
    item: ItineraryItem
    flags: tuple[Flag, ...]
    confirmed_booking: Booking | None
    latest_booking: Booking | None


@dataclass(frozen=True)
class ItineraryView:
    trip: "Trip"
    items: tuple[ItemView, ...]
    missing: tuple[Gap, ...]
    generated_at: datetime
    rules: tuple[str, ...] = RULES


def _overlaps(a: ItineraryItem, b: ItineraryItem) -> bool:
    return a.starts_at < b.ends_at and b.starts_at < a.ends_at


def _can_conflict(a: ItineraryItem, b: ItineraryItem) -> bool:
    return (a.category == "accommodation") == (b.category == "accommodation")


def bookings_by_item(
    bookings: list[Booking],
) -> dict[UUID, tuple[Booking | None, Booking | None]]:
    """item id -> (confirmed booking, most recent booking)."""
    result: dict[UUID, tuple[Booking | None, Booking | None]] = {}
    for b in sorted(bookings, key=lambda b: b.created_at):
        if b.itinerary_item_id is None:
            continue
        confirmed, _ = result.get(b.itinerary_item_id, (None, None))
        if b.status == BookingStatus.CONFIRMED:
            confirmed = b
        result[b.itinerary_item_id] = (confirmed, b)
    return result


def build_view(
    trip: "Trip", items: list[ItineraryItem], bookings: list[Booking], now: datetime
) -> ItineraryView:
    ordered = sorted(items, key=lambda i: i.position)
    booked = bookings_by_item(bookings)
    views = []
    for item in ordered:
        confirmed, latest = booked.get(item.id, (None, None))
        flags: list[Flag] = []
        if confirmed is None and now >= item.valid_until:
            flags.append(Flag("PRICE_EXPIRED",
                              f"The quoted price expired at {item.valid_until.isoformat()}; "
                              "refresh the item before booking."))
        if not item.available:
            flags.append(Flag("OFFER_UNAVAILABLE",
                              "The provider reported this option as unavailable at the last "
                              "quote."))
        if item.starts_at.date() < trip.starts_on or item.ends_at.date() > trip.ends_on:
            flags.append(Flag("OUTSIDE_TRIP_DATES",
                              f"Scheduled {item.starts_at.date()} to {item.ends_at.date()}, "
                              f"outside the trip ({trip.starts_on} to {trip.ends_on})."))
        conflicts = tuple(o.id for o in ordered
                          if o.id != item.id and _can_conflict(item, o) and _overlaps(item, o))
        if conflicts:
            titles = ", ".join(f"'{o.title}'" for o in ordered if o.id in conflicts)
            flags.append(Flag("SCHEDULE_CONFLICT", f"Overlaps with {titles}.", conflicts))
        if item.travelers != trip.travelers:
            flags.append(Flag("PARTY_SIZE_MISMATCH",
                              f"Quoted for {item.travelers} travelers; the trip has "
                              f"{trip.travelers}. Search again for the current party."))
        views.append(ItemView(item, tuple(flags), confirmed, latest))
    return ItineraryView(trip, tuple(views), _gaps(trip, ordered), now)


def _gaps(trip: "Trip", items: list[ItineraryItem]) -> tuple[Gap, ...]:
    if not items:
        return (Gap("EMPTY_ITINERARY", "No options have been added yet."),)
    stays = [i for i in items if i.category == "accommodation"]
    gaps: list[Gap] = []
    with_stay = {s.destination.casefold() for s in stays}
    for destination in trip.destinations:
        if destination.casefold() not in with_stay:
            gaps.append(Gap("NO_ACCOMMODATION_FOR_DESTINATION",
                            f"No accommodation in {destination}."))
    covered: set[date] = set()
    for stay in stays:
        night = stay.starts_at.date()
        while night < stay.ends_at.date():
            covered.add(night)
            night += timedelta(days=1)
    uncovered = []
    night = trip.starts_on
    while night < trip.ends_on:
        if night not in covered:
            uncovered.append(night)
        night += timedelta(days=1)
    for first, last in _ranges(uncovered):
        span = f"night of {first}" if first == last else f"nights of {first} to {last}"
        gaps.append(Gap("NIGHTS_WITHOUT_ACCOMMODATION", f"No accommodation for the {span}."))
    routes = {((i.origin or "").casefold(), i.destination.casefold())
              for i in items if i.category == "transport"}
    for origin, destination in zip(trip.destinations, trip.destinations[1:], strict=False):
        if (origin.casefold(), destination.casefold()) not in routes:
            gaps.append(Gap("MISSING_TRANSPORT",
                            f"Missing transport from {origin} to {destination}."))
    return tuple(gaps)


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    for day in days:
        if ranges and day == ranges[-1][1] + timedelta(days=1):
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    return ranges
