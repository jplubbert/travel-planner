"""The shared rate-limit decorator (domain.rate_limit) with in-memory doubles."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.provider import ProviderRateLimited, ProviderWaitRequired
from app.domain.rate_limit import PROVIDER, RateLimitedProvider

NOW = datetime(2030, 6, 1, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.at = NOW

    def now(self) -> datetime:
        return self.at

    def today(self):  # pragma: no cover - part of the port, unused here
        return self.at.date()


class Limits:
    def __init__(self) -> None:
        self.stored: dict[str, datetime] = {}

    async def not_before(self, provider: str) -> datetime | None:
        return self.stored.get(provider)

    async def hold_until(self, provider: str, until: datetime) -> datetime:
        self.stored[provider] = max(until, self.stored.get(provider, until))
        return self.stored[provider]


class Provider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.calls = error, 0

    async def get_offer(self, offer_id: str) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return offer_id


def guarded(inner: Provider) -> tuple[RateLimitedProvider, Limits, Clock]:
    limits, clock = Limits(), Clock()
    return RateLimitedProvider(inner, limits, clock), limits, clock  # type: ignore[arg-type]


async def test_a_429_is_recorded_and_blocks_the_next_call_without_calling():
    inner = Provider(ProviderRateLimited("slow down", retry_after_seconds=30))
    provider, limits, clock = guarded(inner)
    with pytest.raises(ProviderRateLimited) as first:
        await provider.get_offer("o")
    assert limits.stored[PROVIDER] == NOW + timedelta(seconds=30)
    assert first.value.not_before == NOW + timedelta(seconds=30)

    inner.error = None
    clock.at = NOW + timedelta(seconds=10, milliseconds=500)
    with pytest.raises(ProviderWaitRequired) as blocked:
        await provider.get_offer("o")
    assert inner.calls == 1  # not called again
    assert blocked.value.code == "PROVIDER_RATE_LIMITED"  # the usual error for callers
    assert blocked.value.retry_after_seconds == 20  # 19.5 s left, rounded up
    assert blocked.value.not_before == NOW + timedelta(seconds=30)

    clock.at = NOW + timedelta(seconds=30)  # from not_before on, calls go through
    assert await provider.get_offer("o") == "o" and inner.calls == 2


async def test_a_shorter_429_reports_the_longer_stored_wait():
    """Two processes passed the check before either stored anything; the other one stored
    60 s first. This one's 2 s does not shorten it, and its caller is told 60 s."""

    class RacedLimits(Limits):
        async def not_before(self, provider: str) -> datetime | None:
            return None  # read before the other process stored its wait

    limits, clock = RacedLimits(), Clock()
    limits.stored[PROVIDER] = NOW + timedelta(seconds=60)
    provider = RateLimitedProvider(  # type: ignore[arg-type]
        Provider(ProviderRateLimited("slow down", retry_after_seconds=2)), limits, clock)
    with pytest.raises(ProviderRateLimited) as exc:
        await provider.get_offer("o")
    assert limits.stored[PROVIDER] == NOW + timedelta(seconds=60)  # not shortened
    assert exc.value.retry_after_seconds == 60


async def test_a_429_without_retry_after_is_not_recorded():
    provider, limits, _ = guarded(Provider(ProviderRateLimited("slow down")))
    with pytest.raises(ProviderRateLimited):
        await provider.get_offer("o")
    assert limits.stored == {}


async def test_other_errors_do_not_limit():
    from app.domain.provider import ProviderUnavailable

    provider, limits, _ = guarded(Provider(ProviderUnavailable("down")))
    with pytest.raises(ProviderUnavailable):
        await provider.get_offer("o")
    assert limits.stored == {}
