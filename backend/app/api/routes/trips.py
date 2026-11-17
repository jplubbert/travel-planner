from fastapi import APIRouter, Response, status

from app.api.deps import ClockDep, CurrentUser, Editor, Organizer, UoW, Viewer
from app.api.schemas import (
    MemberOut,
    MemberUpsert,
    TripCreate,
    TripOut,
    TripUpdate,
    UserOut,
    member_out,
    trip_out,
)
from app.domain import planning, trips
from app.domain.access import Role
from app.domain.errors import FieldError, ValidationFailed

router = APIRouter()


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, displayName=user.display_name)


@router.get("/users")
async def list_users(user: CurrentUser, uow: UoW) -> list[UserOut]:
    """Test users that can be added as participants."""
    return [UserOut(id=u.id, displayName=u.display_name) for u in await uow.users.list()]


@router.post("/trips", status_code=status.HTTP_201_CREATED)
async def create_trip(
    body: TripCreate, user: CurrentUser, uow: UoW, clock: ClockDep, response: Response
) -> TripOut:
    trip, role = await trips.create_trip(uow, clock, user.id, body.to_fields())
    response.headers["Location"] = f"/trips/{trip.id}"
    response.headers["ETag"] = f'"{trip.version}"'
    return trip_out(trip, role)


@router.get("/trips")
async def list_trips(user: CurrentUser, uow: UoW) -> list[TripOut]:
    return [trip_out(t, r) for t, r in await trips.list_trips(uow, user.id)]


@router.get("/trips/{trip_id}")
async def get_trip(access: Viewer, uow: UoW, response: Response) -> TripOut:
    trip = await trips.get_trip(uow, access)
    response.headers["ETag"] = f'"{trip.version}"'
    return trip_out(trip, access.role)


@router.patch("/trips/{trip_id}")
async def update_trip(
    body: TripUpdate, access: Editor, uow: UoW, clock: ClockDep, response: Response
) -> TripOut:
    trip = await planning.update_trip(uow, clock, access, body.version, body.to_fields())
    response.headers["ETag"] = f'"{trip.version}"'
    return trip_out(trip, access.role)


@router.get("/trips/{trip_id}/members")
async def list_members(access: Viewer, uow: UoW) -> list[MemberOut]:
    return [member_out(p) for p in await trips.list_members(uow, access)]


@router.put("/trips/{trip_id}/members/{user_id}")
async def set_member(
    user_id: str, body: MemberUpsert, access: Organizer, uow: UoW, clock: ClockDep,
    response: Response,
) -> MemberOut:
    try:
        role = Role(body.role)
    except ValueError:
        raise ValidationFailed([FieldError("role", "must be 'editor' or 'viewer'")]) from None
    participant, created = await trips.set_member_role(uow, clock, access, user_id, role)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return member_out(participant)


@router.delete("/trips/{trip_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: str, access: Organizer, uow: UoW) -> None:
    await trips.remove_member(uow, access, user_id)
