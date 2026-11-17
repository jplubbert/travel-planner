"""US-04 itinerary, US-05 costs. Every mutation requires the trip ``version`` (ADR 0001)."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, StrictInt, StrictStr

from app.api.deps import ClockDep, Editor, Provider, UoW, Viewer
from app.api.schemas import Input, MoneyOut, trip_out
from app.domain import planning
from app.domain.changes import TripChange
from app.domain.costs import ROUNDING
from app.domain.itinerary import ItemView, ItineraryView
from app.domain.money import format_usd

router = APIRouter()


# --- Input --------------------------------------------------------------------------


class Versioned(Input):
    version: StrictInt | None = None  # required; missing -> 400 VERSION_REQUIRED


class AddItem(Versioned):
    offerId: StrictStr | None = None


class Reorder(Versioned):
    itemIds: list[StrictStr] | None = None


# --- Output -------------------------------------------------------------------------


class FlagOut(BaseModel):
    code: str
    message: str
    relatedItemIds: list[UUID]


class QuoteOut(BaseModel):
    price: MoneyOut
    priceType: Literal["estimate"] = "estimate"
    revision: str
    available: bool
    validUntil: datetime
    quotedAt: datetime
    expired: bool


class BookingRefOut(BaseModel):
    bookingId: UUID
    status: str


class ItemOut(BaseModel):
    itemId: UUID
    position: int
    offerId: str
    providerId: str
    category: str
    title: str
    description: str
    destination: str
    origin: str | None
    startsAt: datetime
    endsAt: datetime
    travelers: int
    restrictions: list[str]
    quote: QuoteOut
    booking: BookingRefOut | None
    flags: list[FlagOut]
    addedBy: str
    addedAt: datetime


class GapOut(BaseModel):
    code: str
    message: str


class ItineraryOut(BaseModel):
    tripId: UUID
    tripVersion: int
    items: list[ItemOut]
    missing: list[GapOut]
    rules: list[str]
    generatedAt: datetime


class RefreshOut(ItineraryOut):
    changed: bool
    summary: str


class ChangeOut(BaseModel):
    version: int
    by: str
    at: datetime
    kind: str
    summary: str


class CostLineOut(BaseModel):
    itemId: UUID
    title: str
    category: str
    amount: MoneyOut
    basis: Literal["estimate", "confirmed"]
    bookingId: UUID | None
    bookingStatus: str | None
    note: str


class BudgetOut(BaseModel):
    amount: MoneyOut
    remaining: MoneyOut
    overBudget: bool
    usedPercent: Decimal | None


class CostsOut(BaseModel):
    tripId: UUID
    tripVersion: int
    currency: Literal["USD"] = "USD"
    lines: list[CostLineOut]
    estimatedTotal: MoneyOut
    confirmedTotal: MoneyOut
    total: MoneyOut
    budget: BudgetOut
    rounding: str = ROUNDING


def _money(amount: Decimal) -> MoneyOut:
    return MoneyOut(amount=format_usd(amount))


def _item_out(view: ItemView, now: datetime) -> ItemOut:
    item, booking = view.item, view.confirmed_booking or view.latest_booking
    return ItemOut(
        itemId=item.id, position=item.position, offerId=item.offer_id,
        providerId=item.provider_id, category=item.category, title=item.title,
        description=item.description, destination=item.destination, origin=item.origin,
        startsAt=item.starts_at, endsAt=item.ends_at, travelers=item.travelers,
        restrictions=list(item.restrictions),
        quote=QuoteOut(price=_money(item.price), revision=item.offer_revision,
                       available=item.available, validUntil=item.valid_until,
                       quotedAt=item.quoted_at, expired=now >= item.valid_until),
        booking=BookingRefOut(bookingId=booking.id, status=booking.status) if booking else None,
        flags=[FlagOut(code=f.code, message=f.message, relatedItemIds=list(f.related_item_ids))
               for f in view.flags],
        addedBy=item.added_by, addedAt=item.created_at,
    )


def itinerary_out(view: ItineraryView) -> ItineraryOut:
    return ItineraryOut(
        tripId=view.trip.id, tripVersion=view.trip.version,
        items=[_item_out(v, view.generated_at) for v in view.items],
        missing=[GapOut(code=g.code, message=g.message) for g in view.missing],
        rules=list(view.rules), generatedAt=view.generated_at,
    )


def change_out(change: TripChange) -> ChangeOut:
    return ChangeOut(version=change.version, by=change.actor_id, at=change.at,
                     kind=change.kind, summary=change.summary)


def conflict_details(exc: Any) -> dict[str, Any]:
    """Body details of a 409 TRIP_VERSION_CONFLICT: what is saved now and what changed."""
    return {
        "yourVersion": exc.your_version,
        "currentVersion": exc.current.version,
        "changesSinceYourVersion": [change_out(c).model_dump(mode="json") for c in exc.changes],
        "current": trip_out(exc.current).model_dump(mode="json"),
        "currentItinerary": itinerary_out(exc.itinerary).model_dump(mode="json"),
    }


# --- Routes -------------------------------------------------------------------------


@router.get("/trips/{trip_id}/itinerary")
async def get_itinerary(access: Viewer, uow: UoW, clock: ClockDep) -> ItineraryOut:
    return itinerary_out(await planning.get_itinerary(uow, clock, access))


@router.post("/trips/{trip_id}/itinerary/items", status_code=status.HTTP_201_CREATED)
async def add_item(
    body: AddItem, access: Editor, uow: UoW, provider: Provider, clock: ClockDep
) -> ItineraryOut:
    return itinerary_out(await planning.add_item(uow, provider, clock, access, body.version,
                                                 body.offerId))


@router.delete("/trips/{trip_id}/itinerary/items/{item_id}")
async def remove_item(
    item_id: str, access: Editor, uow: UoW, clock: ClockDep,
    version: int | None = Query(default=None, description="Trip version (required)"),
) -> ItineraryOut:
    return itinerary_out(await planning.remove_item(uow, clock, access, version, item_id))


@router.put("/trips/{trip_id}/itinerary/order")
async def reorder_items(body: Reorder, access: Editor, uow: UoW, clock: ClockDep) -> ItineraryOut:
    return itinerary_out(await planning.reorder_items(uow, clock, access, body.version,
                                                      body.itemIds))


@router.post("/trips/{trip_id}/itinerary/items/{item_id}/refresh")
async def refresh_item(
    item_id: str, body: Versioned, access: Editor, uow: UoW, provider: Provider,
    clock: ClockDep,
) -> RefreshOut:
    result = await planning.refresh_item(uow, provider, clock, access, body.version, item_id)
    return RefreshOut(**itinerary_out(result.view).model_dump(), changed=result.changed,
                      summary=result.summary)


@router.get("/trips/{trip_id}/costs")
async def get_costs(access: Viewer, uow: UoW, response: Response) -> CostsOut:
    costs = await planning.get_costs(uow, access)
    s = costs.summary
    return CostsOut(
        tripId=costs.trip.id, tripVersion=costs.trip.version,
        lines=[CostLineOut(itemId=line.item_id, title=line.title, category=line.category,
                           amount=_money(line.amount), basis=line.basis,
                           bookingId=line.booking_id,
                           bookingStatus=line.booking_status.value if line.booking_status
                           else None, note=line.note)
               for line in s.lines],
        estimatedTotal=_money(s.estimated_total), confirmedTotal=_money(s.confirmed_total),
        total=_money(s.total),
        budget=BudgetOut(amount=_money(s.budget), remaining=_money(s.remaining),
                         overBudget=s.over_budget, usedPercent=s.used_percent),
    )
