"""Trips (US-01) and their participants (US-02)."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from app.domain.access import ASSIGNABLE_ROLES, Role, TripAccess, TripMember
from app.domain.errors import (
    Conflict,
    FieldError,
    NotFound,
    ValidationFailed,
    VersionConflict,
    VersionRequired,
    trip_not_found,
)
from app.domain.money import parse_usd
from app.domain.users import User

if TYPE_CHECKING:
    from app.ports.clock import Clock
    from app.ports.repositories import UnitOfWork

MAX_NAME = 120
MAX_DESTINATIONS = 10
MAX_DESTINATION_LENGTH = 100
MIN_TRAVELERS, MAX_TRAVELERS = 1, 20  # provider contract limits


@dataclass(frozen=True)
class Trip:
    id: UUID
    name: str
    destinations: tuple[str, ...]
    starts_on: date
    ends_on: date
    travelers: int
    budget: Decimal  # USD
    version: int
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TripFields:
    """Raw user input for a trip. Every field optional so the same type serves updates."""

    name: Any = None
    destinations: Any = None
    starts_on: Any = None
    ends_on: Any = None
    travelers: Any = None
    budget: Any = None

    def provided(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _validate(fields: dict[str, Any], *, today: date, dates_changed: bool) -> list[FieldError]:
    """Validates a complete set of trip values; collects every problem at once."""
    errors: list[FieldError] = []

    name = fields["name"]
    if not isinstance(name, str) or not name.strip():
        errors.append(FieldError("name", "is required"))
    elif len(name.strip()) > MAX_NAME:
        errors.append(FieldError("name", f"must be at most {MAX_NAME} characters"))

    destinations = fields["destinations"]
    if not isinstance(destinations, (list, tuple)) or not destinations:
        errors.append(FieldError("destinations", "must list at least one destination"))
    else:
        if len(destinations) > MAX_DESTINATIONS:
            errors.append(
                FieldError("destinations", f"must list at most {MAX_DESTINATIONS} destinations")
            )
        seen: set[str] = set()
        for i, d in enumerate(destinations):
            if not isinstance(d, str) or not d.strip():
                errors.append(FieldError(f"destinations[{i}]", "must not be empty"))
            elif len(d.strip()) > MAX_DESTINATION_LENGTH:
                errors.append(FieldError(
                    f"destinations[{i}]", f"must be at most {MAX_DESTINATION_LENGTH} characters"))
            elif d.strip().casefold() in seen:
                errors.append(FieldError(f"destinations[{i}]", f"'{d.strip()}' is repeated"))
            else:
                seen.add(d.strip().casefold())

    starts_on, ends_on = fields["starts_on"], fields["ends_on"]
    if not isinstance(starts_on, date):
        errors.append(FieldError("startsOn", "is required (YYYY-MM-DD)"))
    if not isinstance(ends_on, date):
        errors.append(FieldError("endsOn", "is required (YYYY-MM-DD)"))
    if isinstance(starts_on, date) and isinstance(ends_on, date):
        if ends_on < starts_on:
            errors.append(FieldError("endsOn", "must be on or after startsOn"))
        if dates_changed and starts_on < today:
            errors.append(FieldError("startsOn", f"must not be in the past (today is {today})"))

    travelers = fields["travelers"]
    if isinstance(travelers, bool) or not isinstance(travelers, int):
        errors.append(FieldError("travelers", "is required (whole number)"))
    elif not MIN_TRAVELERS <= travelers <= MAX_TRAVELERS:
        errors.append(
            FieldError("travelers", f"must be between {MIN_TRAVELERS} and {MAX_TRAVELERS}")
        )

    budget = fields["budget"]
    if not isinstance(budget, Decimal):
        try:
            fields["budget"] = parse_usd(budget)
        except ValueError as exc:
            errors.append(FieldError("budget", f"{exc} (USD)"))
    return errors


def _normalize(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        **fields,
        "name": fields["name"].strip(),
        "destinations": tuple(d.strip() for d in fields["destinations"]),
    }


# --- Use cases ---------------------------------------------------------------


async def create_trip(
    uow: "UnitOfWork", clock: "Clock", actor_id: str, data: TripFields
) -> tuple[Trip, Role]:
    values = {k: getattr(data, k) for k in TripFields.__dataclass_fields__}
    errors = _validate(values, today=clock.today(), dates_changed=True)
    if errors:
        raise ValidationFailed(errors)
    values = _normalize(values)
    now = clock.now()
    trip = Trip(id=uuid4(), version=1, created_by=actor_id, created_at=now, updated_at=now,
                **values)
    # Trip and organizer membership are written in one transaction: never one without the other.
    await uow.trips.add(trip)
    await uow.members.add(TripMember(trip.id, actor_id, Role.ORGANIZER, now))
    await uow.commit()
    return trip, Role.ORGANIZER


async def list_trips(uow: "UnitOfWork", actor_id: str) -> list[tuple[Trip, Role]]:
    return await uow.trips.list_for_user(actor_id)


async def get_trip(uow: "UnitOfWork", access: TripAccess) -> Trip:
    access.require(Role.VIEWER)
    trip = await uow.trips.get(access.trip_id)
    if trip is None:
        raise trip_not_found()
    return trip


async def update_trip(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, expected_version: int | None,
    changes: TripFields,
) -> Trip:
    """ADR 0001: authorization first, then validation, then an atomic version check."""
    access.require(Role.EDITOR)
    if expected_version is None:
        raise VersionRequired()
    current = await get_trip(uow, access)
    provided = changes.provided()
    values = {k: getattr(current, k) for k in TripFields.__dataclass_fields__} | provided
    dates_changed = any(
        k in provided and provided[k] != getattr(current, k) for k in ("starts_on", "ends_on")
    )
    errors = _validate(values, today=clock.today(), dates_changed=dates_changed)
    if errors:
        raise ValidationFailed(errors)
    updated = replace(current, **_normalize(values), version=expected_version + 1,
                      updated_at=clock.now())
    if not await uow.trips.update(updated, expected_version=expected_version):
        await uow.rollback()
        latest = await get_trip(uow, access)
        raise VersionConflict(latest)
    await uow.commit()
    return updated


# --- Participants (US-02) ----------------------------------------------------


@dataclass(frozen=True)
class Participant:
    user: User
    role: Role
    added_at: datetime


async def list_members(uow: "UnitOfWork", access: TripAccess) -> list[Participant]:
    access.require(Role.VIEWER)
    members = await uow.members.list(access.trip_id)
    users = {u.id: u for u in await uow.users.list()}
    return [Participant(users[m.user_id], m.role, m.added_at) for m in members]


async def set_member_role(
    uow: "UnitOfWork", clock: "Clock", access: TripAccess, user_id: str, role: Role
) -> tuple[Participant, bool]:
    """Adds the user or changes their role. Returns (participant, created)."""
    access.require(Role.ORGANIZER)
    if role not in ASSIGNABLE_ROLES:
        raise ValidationFailed([FieldError("role", "must be 'editor' or 'viewer'")])
    user = await uow.users.get(user_id)
    if user is None:
        raise NotFound("USER_NOT_FOUND", f"User '{user_id}' does not exist.")
    existing = await uow.members.get(access.trip_id, user_id)
    if existing is not None and existing.role == Role.ORGANIZER:
        raise Conflict("ORGANIZER_ROLE_FIXED", "The organizer's role cannot be changed.")
    if existing is None:
        member = TripMember(access.trip_id, user_id, role, clock.now())
        await uow.members.add(member)
    else:
        member = replace(existing, role=role)
        await uow.members.update_role(access.trip_id, user_id, role)
    await uow.commit()
    return Participant(user, member.role, member.added_at), existing is None


async def remove_member(uow: "UnitOfWork", access: TripAccess, user_id: str) -> None:
    access.require(Role.ORGANIZER)
    existing = await uow.members.get(access.trip_id, user_id)
    if existing is None:
        raise NotFound("MEMBER_NOT_FOUND", f"User '{user_id}' is not a participant.")
    if existing.role == Role.ORGANIZER:
        raise Conflict("ORGANIZER_ROLE_FIXED", "The organizer cannot be removed.")
    await uow.members.remove(access.trip_id, user_id)
    await uow.commit()
