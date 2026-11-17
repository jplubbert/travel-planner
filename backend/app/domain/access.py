"""Trip roles and authorization. Every entry point (HTTP, status subscriptions, MCP tools)
must obtain a ``TripAccess`` through ``load_access`` before touching trip data."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.errors import Forbidden, trip_not_found

if TYPE_CHECKING:
    from app.ports.repositories import UnitOfWork


class Role(StrEnum):
    ORGANIZER = "organizer"
    EDITOR = "editor"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return {Role.VIEWER: 1, Role.EDITOR: 2, Role.ORGANIZER: 3}[self]

    def allows(self, required: "Role") -> bool:
        """Editors inherit viewer capabilities; organizers inherit both."""
        return self.rank >= required.rank


# Roles an organizer may grant. There is exactly one organizer: the trip creator.
ASSIGNABLE_ROLES = (Role.EDITOR, Role.VIEWER)


@dataclass(frozen=True)
class TripMember:
    trip_id: UUID
    user_id: str
    role: Role
    added_at: datetime


@dataclass(frozen=True)
class TripAccess:
    """Proof that ``user_id`` participates in ``trip_id`` with ``role``."""

    trip_id: UUID
    user_id: str
    role: Role

    def require(self, required: Role) -> None:
        if not self.role.allows(required):
            raise Forbidden(
                f"This action requires the {required} role; you are a {self.role} of this trip."
            )


def parse_trip_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise trip_not_found() from None


async def load_access(uow: "UnitOfWork", trip_id: UUID, user_id: str) -> TripAccess:
    member = await uow.members.get(trip_id, user_id)
    if member is None:
        raise trip_not_found()
    return TripAccess(trip_id=trip_id, user_id=user_id, role=member.role)
