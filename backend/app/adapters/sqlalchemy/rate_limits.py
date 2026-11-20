"""Shared provider rate limit in PostgreSQL (``provider_rate_limits``)."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.sqlalchemy.models import ProviderRateLimitRow


class SqlProviderRateLimits:
    """Each call is its own short transaction, outside any unit of work."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def not_before(self, provider: str) -> datetime | None:
        async with self.sessions() as session:
            value: datetime | None = await session.scalar(
                select(ProviderRateLimitRow.not_before)
                .where(ProviderRateLimitRow.provider == provider))
            return value

    async def hold_until(self, provider: str, until: datetime) -> datetime:
        # One atomic upsert: concurrent 429s keep the longest wait, never a shorter one.
        new = insert(ProviderRateLimitRow).values(provider=provider, not_before=until)
        upsert = new.on_conflict_do_update(
            index_elements=[ProviderRateLimitRow.provider],
            set_={"not_before": func.greatest(ProviderRateLimitRow.not_before,
                                              new.excluded.not_before)},
        ).returning(ProviderRateLimitRow.not_before)
        async with self.sessions() as session:
            stored: datetime = (await session.execute(upsert)).scalar_one()
            await session.commit()
            return stored
