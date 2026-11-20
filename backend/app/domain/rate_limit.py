"""A provider rate limit shared by every process (API replicas and workers).

When any call gets a 429, its Retry-After is stored as ``not_before`` for the provider
(never shortening a longer wait already stored). Until then no process calls the provider:
the call fails at once with ``ProviderWaitRequired``, a ``ProviderRateLimited`` with the
remaining seconds, so user requests answer immediately with the usual rate-limit error and
the worker reschedules its job for ``not_before`` (``reservations.sync_booking``).

The contract's 429 does not say which supplier was limited, so the limit applies to the
provider as a whole (docs/assumptions.md).
"""

import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from app.domain.provider import (
    BookingRequest,
    Offer,
    ProviderBooking,
    ProviderRateLimited,
    ProviderWaitRequired,
    SearchQuery,
    SearchResult,
)

if TYPE_CHECKING:
    from app.ports.clock import Clock
    from app.ports.provider import ProviderClient, ProviderRateLimits

PROVIDER = "travel-provider"  # the one external provider the application calls


class RateLimitedProvider:
    """A ``ProviderClient`` that honours and records the shared rate limit."""

    def __init__(self, inner: "ProviderClient", limits: "ProviderRateLimits",
                 clock: "Clock", provider: str = PROVIDER) -> None:
        self.inner = inner
        self.limits = limits
        self.clock = clock
        self.provider = provider

    async def _call[T](self, call: Callable[[], Awaitable[T]]) -> T:
        until = await self.limits.not_before(self.provider)
        now = self.clock.now()
        if until is not None and now < until:
            raise ProviderWaitRequired(
                f"Not sent: the travel provider asked to wait until {until.isoformat()}.",
                retry_after_seconds=math.ceil((until - now).total_seconds()),
                not_before=until)
        try:
            return await call()
        except ProviderRateLimited as exc:
            if exc.retry_after_seconds:
                now = self.clock.now()
                exc.not_before = await self.limits.hold_until(
                    self.provider, now + timedelta(seconds=exc.retry_after_seconds))
                # A longer wait stored by someone else wins: report that one.
                exc.retry_after_seconds = max(
                    exc.retry_after_seconds,
                    math.ceil((exc.not_before - now).total_seconds()))
            raise

    async def search(self, query: SearchQuery) -> SearchResult:
        return await self._call(lambda: self.inner.search(query))

    async def get_offer(self, offer_id: str) -> Offer:
        return await self._call(lambda: self.inner.get_offer(offer_id))

    async def create_booking(
        self, request: BookingRequest, *, idempotency_key: str
    ) -> ProviderBooking:
        return await self._call(
            lambda: self.inner.create_booking(request, idempotency_key=idempotency_key))

    async def get_booking(self, booking_id: str) -> ProviderBooking:
        return await self._call(lambda: self.inner.get_booking(booking_id))

    async def get_booking_by_reference(self, client_reference: str) -> ProviderBooking:
        return await self._call(lambda: self.inner.get_booking_by_reference(client_reference))
