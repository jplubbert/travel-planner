from types import TracebackType

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.adapters.sqlalchemy.repositories import (
    SqlBookingRepository,
    SqlDocumentRepository,
    SqlItineraryRepository,
    SqlJobRepository,
    SqlMemberRepository,
    SqlTripRepository,
    SqlUserRepository,
)
from app.ports.repositories import (
    BookingRepository,
    DocumentRepository,
    ItineraryRepository,
    JobRepository,
    MemberRepository,
    TripRepository,
    UserRepository,
)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


class SqlUnitOfWork:
    """One transaction per unit of work. Anything not committed is rolled back on exit,
    so a failure mid-operation never leaves a partial write."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __aenter__(self) -> "SqlUnitOfWork":
        self.session = self._sessions()
        # Typed as the ports so mypy checks each adapter satisfies its protocol.
        self.users: UserRepository = SqlUserRepository(self.session)
        self.trips: TripRepository = SqlTripRepository(self.session)
        self.members: MemberRepository = SqlMemberRepository(self.session)
        self.itinerary: ItineraryRepository = SqlItineraryRepository(self.session)
        self.bookings: BookingRepository = SqlBookingRepository(self.session)
        self.documents: DocumentRepository = SqlDocumentRepository(self.session)
        self.jobs: JobRepository = SqlJobRepository(self.session)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
