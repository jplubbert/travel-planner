from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictStr

from app.api.deps import ClockDep, Provider, UoW, Viewer
from app.api.schemas import Input, MoneyOut
from app.domain.money import format_usd
from app.domain.provider import (
    Offer,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.domain.search import SearchInput, SearchOutcome, SearchOutcomeKind, search_offers

router = APIRouter()

DISCLAIMER = (
    "Search results are provider quotes, not bookings. Prices are estimates for the whole "
    "party, valid only until each offer's validUntil; they do not hold availability and may "
    "change before booking."
)

MESSAGES = {
    SearchOutcomeKind.COMPLETE: "All queried suppliers answered.",
    SearchOutcomeKind.PARTIAL: (
        "Some suppliers did not answer, so these results are incomplete. "
        "The offers shown are still valid quotes from the suppliers that answered."
    ),
    SearchOutcomeKind.EMPTY: "All queried suppliers answered, but no options match.",
    SearchOutcomeKind.FAILED: "The search could not be completed; no results are available.",
}


class SearchBody(Input):
    category: StrictStr
    destination: StrictStr
    origin: StrictStr | None = None
    startsOn: date | None = None
    endsOn: date | None = None
    providerId: StrictStr | None = None


class QueryOut(BaseModel):
    category: str
    destination: str
    origin: str | None
    startsOn: date
    endsOn: date
    travelers: int
    providerId: str | None


class OfferOut(BaseModel):
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
    price: MoneyOut
    priceType: Literal["estimate"] = "estimate"
    revision: str
    available: bool
    validUntil: datetime
    quoteExpired: bool
    restrictions: list[str]


class FailedSupplierOut(BaseModel):
    providerId: str
    code: str
    message: str


class SearchErrorOut(BaseModel):
    code: str
    message: str
    retryAfterSeconds: int | None = None
    providerRequestId: str | None = None


class SearchOut(BaseModel):
    outcome: SearchOutcomeKind
    message: str
    disclaimer: str = DISCLAIMER
    query: QueryOut
    searchedAt: datetime | None
    receivedAt: datetime
    offers: list[OfferOut]
    failedSuppliers: list[FailedSupplierOut]
    error: SearchErrorOut | None = None
    requestId: str | None = None


def _offer_out(offer: Offer, now: datetime) -> OfferOut:
    return OfferOut(
        offerId=offer.id, providerId=offer.provider_id, category=offer.category,
        title=offer.title, description=offer.description, destination=offer.destination,
        origin=offer.origin, startsAt=offer.starts_at, endsAt=offer.ends_at,
        travelers=offer.travelers, price=MoneyOut(amount=format_usd(offer.price)),
        revision=offer.revision, available=offer.available, validUntil=offer.valid_until,
        quoteExpired=offer.quote_expired(now), restrictions=list(offer.restrictions),
    )


def search_out(outcome: SearchOutcome, request_id: str | None) -> SearchOut:
    q = outcome.query
    error = None
    if outcome.error is not None:
        error = SearchErrorOut(
            code=outcome.error.code, message=outcome.error.message,
            retryAfterSeconds=getattr(outcome.error, "retry_after_seconds", None),
            providerRequestId=outcome.error.provider_request_id,
        )
    return SearchOut(
        outcome=outcome.kind, message=MESSAGES[outcome.kind],
        query=QueryOut(category=q.category, destination=q.destination, origin=q.origin,
                       startsOn=q.starts_on, endsOn=q.ends_on, travelers=q.travelers,
                       providerId=q.provider_id),
        searchedAt=outcome.searched_at, receivedAt=outcome.received_at,
        offers=[_offer_out(o, outcome.received_at) for o in outcome.offers],
        failedSuppliers=[FailedSupplierOut(providerId=w.provider_id, code=w.code,
                                           message=w.message)
                         for w in outcome.failed_suppliers],
        error=error, requestId=request_id,
    )


@router.post("/trips/{trip_id}/offers/search", response_model=SearchOut,
             responses={503: {"model": SearchOut}, 504: {"model": SearchOut}})
async def search(
    body: SearchBody, access: Viewer, uow: UoW, provider: Provider, clock: ClockDep,
    request: Request,
) -> Response:
    outcome = await search_offers(uow, provider, clock, access, SearchInput(
        category=body.category, destination=body.destination, origin=body.origin,
        starts_on=body.startsOn, ends_on=body.endsOn, provider_id=body.providerId,
    ))
    payload = search_out(outcome, request.state.request_id).model_dump(mode="json")
    if outcome.kind != SearchOutcomeKind.FAILED:
        return JSONResponse(payload)
    # A failed search is still a full SearchOut, with an error status for clients/proxies.
    headers = {}
    if isinstance(outcome.error, ProviderRateLimited) and outcome.error.retry_after_seconds:
        headers["Retry-After"] = str(outcome.error.retry_after_seconds)
    status = 504 if isinstance(outcome.error, ProviderTimeout) else (
        502 if outcome.error is not None and outcome.error.delivery_uncertain else 503)
    return JSONResponse(payload, status_code=status, headers=headers)
