"""Versioned changes to a trip and its itinerary (US-01 edits, US-04, US-06), and the
itinerary and cost reads (US-04, US-05).

Every mutation follows ADR 0001: authorize, require the caller's version, validate, then
atomically bump the trip version (compare-and-set) in the same transaction as the write
and a ``TripChange`` record. The compare-and-set locks the trip row, so concurrent
mutations of one trip are serialized and exactly one writer per version wins.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.domain.access import Role, TripAccess
from app.domain.changes import TripChange
from app.domain.costs import CostSummary, summarize
from app.domain.errors import (
    Conflict,
    FieldError,
    NotFound,
    ValidationFailed,
    VersionConflict,
    VersionRequired,
    trip_not_found,
)
from app.domain.itinerary import ACTIVE_BOOKING, ItineraryItem, ItineraryView, build_view
from app.domain.money import format_usd
from app.domain.provider import ProviderNotFound
from app.domain.trips import (
    Trip,
    TripFields,
    get_trip,
    normalize_trip_values,
    validate_trip_values,
)

if TYPE_CHECKING:
    from app.ports.clock import Clock
    from app.ports.provider import ProviderClient
    from app.ports.repositories import UnitOfWork


# --- Reads ------------------------------------------------------------------------------


async def _view(uow: "UnitOfWork", clock: "Clock", trip: Trip) -> ItineraryView:
    items = await uow.itinerary.list_for_trip(trip.id)
    bookings = await uow.bookings.list_for_trip(trip.id)
    return build_view(trip, items, bookings, clock.now())


async def get_itinerary(uow: "UnitOfWork", clock: "Clock", access: TripAccess) -> ItineraryView:
    access.require(Role.VIEWER)
    return await _view(uow, clock, await get_trip(uow, access))


@dataclass(frozen=True)
class TripCosts:
    trip: Trip
    summary: CostSummary


async def get_costs(uow: "UnitOfWork", access: TripAccess) -> TripCosts:
    access.require(Role.VIEWER)
    trip = await get_trip(uow, access)
    items = await uow.itinerary.list_for_trip(trip.id)
    bookings = await uow.bookings.list_for_trip(trip.id)
    return TripCosts(trip, summarize(items, bookings, trip.budget))


# --- Version handling ---------------------------------------------------------------------


async def _conflict(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, your_version: int
) -> VersionConflict:
    await uow.rollback()
    trip = await get_trip(uow, access)
    changes = await uow.changes.list_since(trip.id, your_version)
    return VersionConflict(trip, your_version, tuple(changes), await _view(uow, clock, trip))


async def _start(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, expected_version: int | None
) -> Trip:
    """Authorization before version (a viewer gets 403, never 409), then the version check.

    The trip row is locked from here: under READ COMMITTED each statement sees the latest
    commit, so without the lock a validation read could see a newer version than the one
    checked (e.g. a concurrent removal turning a version conflict into a 422)."""
    access.require(Role.EDITOR)
    if expected_version is None:
        raise VersionRequired()
    trip = await uow.trips.get_for_update(access.trip_id)
    if trip is None:
        raise trip_not_found()
    if trip.version != expected_version:
        raise await _conflict(uow, clock, access, expected_version)
    return trip


async def _commit_change(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, expected_version: int, kind: str,
    summary: str,
) -> None:
    """Claims the next version; raises VersionConflict if someone else claimed it first.
    Must run before the change is written; the caller commits."""
    now = clock.now()
    if not await uow.trips.bump_version(access.trip_id, expected_version=expected_version,
                                        now=now):
        raise await _conflict(uow, clock, access, expected_version)
    await uow.changes.add(TripChange(access.trip_id, expected_version + 1, access.user_id,
                                     kind, summary, now))


async def _item(uow: "UnitOfWork", trip_id: UUID, raw_item_id: str) -> ItineraryItem:
    try:
        item = await uow.itinerary.get(UUID(raw_item_id))
    except ValueError:
        item = None
    if item is None or item.trip_id != trip_id:
        raise NotFound("ITEM_NOT_FOUND", "Itinerary item not found.")
    return item


# --- Trip details (US-01) -----------------------------------------------------------------


async def update_trip(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, expected_version: int | None,
    changes: TripFields,
) -> Trip:
    current = await _start(uow, clock, access, expected_version)
    assert expected_version is not None
    provided = changes.provided()
    values = {k: getattr(current, k) for k in TripFields.__dataclass_fields__} | provided
    dates_changed = any(
        k in provided and provided[k] != getattr(current, k) for k in ("starts_on", "ends_on"))
    errors = validate_trip_values(values, today=clock.today(), dates_changed=dates_changed)
    if errors:
        raise ValidationFailed(errors)
    updated = replace(current, **normalize_trip_values(values), version=expected_version + 1,
                      updated_at=clock.now())
    changed = sorted(k for k in provided if getattr(updated, k) != getattr(current, k))
    if not await uow.trips.update(updated, expected_version=expected_version):
        raise await _conflict(uow, clock, access, expected_version)
    summary = "Updated " + (", ".join(changed) if changed else "nothing (same values)")
    await uow.changes.add(TripChange(current.id, updated.version, access.user_id,
                                     "trip_updated", summary, updated.updated_at))
    await uow.commit()
    return updated


# --- Itinerary (US-04) --------------------------------------------------------------------


async def add_item(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", access: TripAccess,
    expected_version: int | None, offer_id: object,
) -> ItineraryView:
    """The offer is fetched from the provider: the client's copy of price or terms is never
    trusted."""
    trip = await _start(uow, clock, access, expected_version)
    assert expected_version is not None
    if not isinstance(offer_id, str) or not 1 <= len(offer_id) <= 128:
        raise ValidationFailed([FieldError("offerId", "is required (1-128 characters)")])
    items = await uow.itinerary.list_for_trip(trip.id)
    if any(i.offer_id == offer_id for i in items):
        raise Conflict("ITEM_ALREADY_IN_ITINERARY", "This offer is already in the itinerary.")
    await uow.rollback()  # do not hold a database connection during the provider call

    try:
        offer = await provider.get_offer(offer_id)
    except ProviderNotFound:
        raise ValidationFailed([FieldError("offerId", "the provider has no such offer")]) \
            from None
    if offer.travelers != trip.travelers:
        raise ValidationFailed([FieldError(
            "offerId", f"this offer is for {offer.travelers} travelers but the trip has "
                       f"{trip.travelers}; search again from the trip")])
    if not offer.available:
        raise Conflict("OFFER_UNAVAILABLE", "The provider reports this offer as unavailable.")

    await _commit_change(uow, clock, access, expected_version, "item_added",
                         f"Added '{offer.title}' ({format_usd(offer.price)} USD estimate)")
    # The version claim above guarantees ``items`` is still the current itinerary.
    item = ItineraryItem.from_offer(offer, id=uuid4(), trip_id=trip.id, position=len(items),
                                    added_by=access.user_id, now=clock.now())
    await uow.itinerary.add(item)
    await uow.commit()
    return await _view(uow, clock, await get_trip(uow, access))


async def remove_item(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, expected_version: int | None,
    raw_item_id: str,
) -> ItineraryView:
    trip = await _start(uow, clock, access, expected_version)
    assert expected_version is not None
    item = await _item(uow, trip.id, raw_item_id)
    bookings = [b for b in await uow.bookings.list_for_trip(trip.id)
                if b.itinerary_item_id == item.id and b.status in ACTIVE_BOOKING]
    if bookings:
        raise Conflict("ITEM_HAS_BOOKING",
                       f"'{item.title}' has a {bookings[-1].status} booking and cannot be "
                       "removed; cancellation is out of scope.")
    await _commit_change(uow, clock, access, expected_version, "item_removed",
                         f"Removed '{item.title}'")
    await uow.itinerary.remove(item.id)
    remaining = [i for i in await uow.itinerary.list_for_trip(trip.id) if i.id != item.id]
    await uow.itinerary.set_positions({i.id: n for n, i in enumerate(remaining)})
    await uow.commit()
    return await _view(uow, clock, await get_trip(uow, access))


async def reorder_items(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, expected_version: int | None,
    raw_item_ids: object,
) -> ItineraryView:
    trip = await _start(uow, clock, access, expected_version)
    assert expected_version is not None
    items = await uow.itinerary.list_for_trip(trip.id)
    current = {str(i.id) for i in items}
    if (not isinstance(raw_item_ids, list) or len(raw_item_ids) != len(items)
            or set(map(str, raw_item_ids)) != current):
        raise ValidationFailed([FieldError(
            "itemIds", "must list every item of the itinerary exactly once")])
    await _commit_change(uow, clock, access, expected_version, "items_reordered",
                         "Reordered the itinerary")
    await uow.itinerary.set_positions({UUID(str(i)): n for n, i in enumerate(raw_item_ids)})
    await uow.commit()
    return await _view(uow, clock, await get_trip(uow, access))


@dataclass(frozen=True)
class RefreshResult:
    view: ItineraryView
    changed: bool
    summary: str


async def refresh_item(
    uow: "UnitOfWork", provider: "ProviderClient", clock: "Clock", access: TripAccess,
    expected_version: int | None, raw_item_id: str,
) -> RefreshResult:
    """Replaces the item's quote with the provider's current one (ADR 0004)."""
    trip = await _start(uow, clock, access, expected_version)
    assert expected_version is not None
    item = await _item(uow, trip.id, raw_item_id)
    await uow.rollback()
    try:
        offer = await provider.get_offer(item.offer_id)
    except ProviderNotFound:
        raise Conflict("OFFER_NO_LONGER_OFFERED",
                       f"The provider no longer offers '{item.title}'. Remove it or choose "
                       "another option.") from None
    if not item.quote_differs(offer):
        return RefreshResult(await _view(uow, clock, trip), False,
                             "The quote is unchanged; nothing was saved.")
    parts = []
    if offer.price != item.price:
        parts.append(f"price {format_usd(item.price)} -> {format_usd(offer.price)} USD")
    if offer.available != item.available:
        parts.append("now available" if offer.available else "now unavailable")
    if (offer.starts_at, offer.ends_at) != (item.starts_at, item.ends_at):
        parts.append("schedule changed")
    parts.append(f"valid until {offer.valid_until.isoformat()}")
    summary = f"Refreshed quote for '{item.title}': " + ", ".join(parts)
    await _commit_change(uow, clock, access, expected_version, "item_quote_refreshed", summary)
    await uow.itinerary.save(item.with_quote(offer, clock.now()))
    await uow.commit()
    return RefreshResult(await _view(uow, clock, await get_trip(uow, access)), True, summary)
