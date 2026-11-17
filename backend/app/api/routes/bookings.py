"""US-07 booking requests and US-08 booking status."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, StrictStr

from app.api.deps import ClockDep, Editor, Provider, UoW, Viewer
from app.api.schemas import Input, MoneyOut
from app.domain import reservations
from app.domain.bookings import BookingStatus, next_step
from app.domain.money import format_usd
from app.domain.reservations import BookingView

router = APIRouter()


class BookingRequestBody(Input):
    expectedRevision: StrictStr | None = None
    expectedPrice: dict[str, Any] | None = None


class LastErrorOut(BaseModel):
    code: str
    message: str | None = None
    providerRequestId: str | None = None
    retryAfterSeconds: int | None = None
    at: datetime | None = None


class FailureOut(BaseModel):
    code: str | None
    message: str | None


class BookingOut(BaseModel):
    bookingId: UUID
    itemId: UUID | None
    itemTitle: str | None
    offerId: str
    status: BookingStatus
    # True only when the provider confirmed the booking. "unknown" is never presented as
    # success and "failed" (accepted, then failed) is distinct from "rejected" (never
    # accepted).
    booked: bool
    nextStep: str
    canRetry: bool
    expectedPrice: MoneyOut
    expectedRevision: str
    acceptedPrice: MoneyOut | None
    acceptedOffer: dict[str, Any] | None
    providerBookingId: str | None
    clientReference: str
    failure: FailureOut | None
    lastError: LastErrorOut | None
    currentTerms: dict[str, Any] | None
    requestedBy: str
    createdAt: datetime
    submittedAt: datetime | None
    updatedAt: datetime


def booking_out(view: BookingView) -> BookingOut:
    b = view.booking
    return BookingOut(
        bookingId=b.id, itemId=b.itinerary_item_id, itemTitle=view.item_title,
        offerId=b.offer_id, status=b.status, booked=b.status == BookingStatus.CONFIRMED,
        nextStep=next_step(b), canRetry=b.status == BookingStatus.DRAFT,
        expectedPrice=MoneyOut(amount=format_usd(b.expected_price)),
        expectedRevision=b.expected_revision,
        acceptedPrice=MoneyOut(amount=format_usd(b.accepted_price))
        if b.accepted_price is not None else None,
        acceptedOffer=b.accepted_offer, providerBookingId=b.provider_booking_id,
        clientReference=b.client_reference,
        failure=FailureOut(code=b.failure_code, message=b.failure_message)
        if b.failure_code else None,
        lastError=LastErrorOut(**b.last_error) if b.last_error else None,
        currentTerms=b.rejection_terms, requestedBy=b.requested_by, createdAt=b.created_at,
        submittedAt=b.submitted_at, updatedAt=b.updated_at,
    )


@router.post("/trips/{trip_id}/itinerary/items/{item_id}/bookings",
             status_code=status.HTTP_201_CREATED)
async def request_booking(
    item_id: str, body: BookingRequestBody, access: Editor, uow: UoW, provider: Provider,
    clock: ClockDep,
) -> BookingOut:
    """Books one itinerary item with the terms the user reviewed. 201 means an attempt was
    recorded; its ``status`` says what happened (it may be unknown, draft or rejected)."""
    return booking_out(await reservations.request_booking(
        uow, provider, clock, access, item_id, body.expectedRevision, body.expectedPrice))


@router.post("/trips/{trip_id}/bookings/{booking_id}/retry")
async def retry_booking(
    booking_id: str, access: Editor, uow: UoW, provider: Provider, clock: ClockDep
) -> BookingOut:
    return booking_out(await reservations.retry_booking(uow, provider, clock, access,
                                                        booking_id))


@router.get("/trips/{trip_id}/bookings")
async def list_bookings(access: Viewer, uow: UoW) -> list[BookingOut]:
    return [booking_out(v) for v in await reservations.list_bookings(uow, access)]


@router.get("/trips/{trip_id}/bookings/{booking_id}")
async def get_booking(booking_id: str, access: Viewer, uow: UoW) -> BookingOut:
    return booking_out(await reservations.get_booking(uow, access, booking_id))
