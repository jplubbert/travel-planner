"""What the status channel watches for one trip (ADR 0005).

The channel only *announces* that something changed; clients always re-read the state
with the regular GET endpoints, so a missed or duplicated event cannot mislead them.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.access import Role, TripAccess, load_access

if TYPE_CHECKING:
    from app.ports.repositories import UnitOfWork


@dataclass(frozen=True)
class Change:
    kind: str  # "trip" | "booking" | "document"
    id: str
    status: str | None
    version: int | None = None


Digest = dict[tuple[str, str], tuple[str | None, str]]


async def digest(uow: "UnitOfWork", access: TripAccess) -> Digest:
    """Current state fingerprint. Re-checks access every time: raises NotFound once the
    user is no longer a participant, so a stream never outlives its permission."""
    current = await load_access(uow, access.trip_id, access.user_id)
    current.require(Role.VIEWER)
    trip = await uow.trips.get(access.trip_id)
    assert trip is not None
    state: Digest = {("trip", str(trip.id)): (str(trip.version), trip.updated_at.isoformat())}
    for b in await uow.bookings.list_for_trip(trip.id):
        state[("booking", str(b.id))] = (b.status.value, b.updated_at.isoformat())
    for d in await uow.documents.list_for_trip(trip.id):
        state[("document", str(d.id))] = (d.status.value, d.updated_at.isoformat())
    return state


def changes(before: Digest, after: Digest) -> list[Change]:
    result = []
    for key, value in after.items():
        if before.get(key) != value:
            kind, ident = key
            if kind == "trip":
                result.append(Change(kind, ident, None, int(value[0] or 0)))
            else:
                result.append(Change(kind, ident, value[0]))
    return result
