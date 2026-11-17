"""Request-scoped dependencies: unit of work, identity and trip authorization."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request

from app.adapters.sqlalchemy.uow import SqlUnitOfWork
from app.api.errors import Unauthenticated
from app.domain.access import Role, TripAccess, load_access, parse_trip_id
from app.domain.users import User
from app.ports.clock import Clock
from app.ports.provider import ProviderClient


async def get_uow(request: Request) -> AsyncIterator[SqlUnitOfWork]:
    async with SqlUnitOfWork(request.app.state.sessions) as uow:
        yield uow


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_provider(request: Request) -> ProviderClient:
    provider: ProviderClient = request.app.state.provider
    return provider


UoW = Annotated[SqlUnitOfWork, Depends(get_uow)]
ClockDep = Annotated[Clock, Depends(get_clock)]
Provider = Annotated[ProviderClient, Depends(get_provider)]


async def current_user(
    uow: UoW, x_user_id: Annotated[str | None, Header()] = None
) -> User:
    """Test identity (no production auth, as the PRD allows): the X-User-Id header names a
    pre-seeded user."""
    if not x_user_id:
        raise Unauthenticated("Send the X-User-Id header with a test user id (e.g. 'ana').")
    user = await uow.users.get(x_user_id)
    if user is None:
        raise Unauthenticated(f"Unknown user '{x_user_id}'.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(role: Role) -> Callable[..., Awaitable[TripAccess]]:
    """Resolves the caller's membership in the path's trip. Nonparticipants get 404
    (indistinguishable from a missing trip); participants without ``role`` get 403."""

    async def dependency(trip_id: str, user: CurrentUser, uow: UoW) -> TripAccess:
        access = await load_access(uow, parse_trip_id(trip_id), user.id)
        access.require(role)
        return access

    return dependency


Viewer = Annotated[TripAccess, Depends(require_role(Role.VIEWER))]
Editor = Annotated[TripAccess, Depends(require_role(Role.EDITOR))]
Organizer = Annotated[TripAccess, Depends(require_role(Role.ORGANIZER))]
