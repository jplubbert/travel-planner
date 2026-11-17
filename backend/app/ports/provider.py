from typing import Protocol

from app.domain.provider import (
    BookingRequest,
    Offer,
    ProviderBooking,
    SearchQuery,
    SearchResult,
)


class ProviderClient(Protocol):
    """The external travel provider (assignment/openAPI.json).

    Every method finishes within the configured timeout and raises a subclass of
    ``app.domain.provider.ProviderError`` on any failure.
    """

    async def search(self, query: SearchQuery) -> SearchResult: ...
    async def get_offer(self, offer_id: str) -> Offer: ...

    async def create_booking(
        self, request: BookingRequest, *, idempotency_key: str
    ) -> ProviderBooking: ...

    async def get_booking(self, booking_id: str) -> ProviderBooking: ...
    async def get_booking_by_reference(self, client_reference: str) -> ProviderBooking: ...
