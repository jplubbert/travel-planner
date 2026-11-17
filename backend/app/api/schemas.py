"""HTTP contract of the application (camelCase JSON)."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.domain.access import Role
from app.domain.money import format_usd
from app.domain.trips import Participant, Trip, TripFields


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


AmountStr = Annotated[StrictStr, Field(description="USD decimal string, e.g. '1500.00'")]


class TripCreate(Input):
    name: StrictStr
    destinations: list[StrictStr]
    startsOn: date
    endsOn: date
    travelers: StrictInt
    budget: AmountStr

    def to_fields(self) -> TripFields:
        return TripFields(name=self.name, destinations=self.destinations,
                          starts_on=self.startsOn, ends_on=self.endsOn,
                          travelers=self.travelers, budget=self.budget)


class TripUpdate(Input):
    version: StrictInt | None = Field(
        default=None, description="Version of the trip you loaded (required; ADR 0001)")
    name: StrictStr | None = None
    destinations: list[StrictStr] | None = None
    startsOn: date | None = None
    endsOn: date | None = None
    travelers: StrictInt | None = None
    budget: AmountStr | None = None

    def to_fields(self) -> TripFields:
        return TripFields(name=self.name, destinations=self.destinations,
                          starts_on=self.startsOn, ends_on=self.endsOn,
                          travelers=self.travelers, budget=self.budget)


class MoneyOut(BaseModel):
    amount: str
    currency: Literal["USD"] = "USD"


class TripOut(BaseModel):
    id: UUID
    name: str
    destinations: list[str]
    startsOn: date
    endsOn: date
    travelers: int
    budget: MoneyOut
    version: int
    createdBy: str
    createdAt: datetime
    updatedAt: datetime
    myRole: Role | None = None


def trip_out(trip: Trip, role: Role | None = None) -> TripOut:
    return TripOut(
        id=trip.id, name=trip.name, destinations=list(trip.destinations),
        startsOn=trip.starts_on, endsOn=trip.ends_on, travelers=trip.travelers,
        budget=MoneyOut(amount=format_usd(trip.budget)), version=trip.version,
        createdBy=trip.created_by, createdAt=trip.created_at, updatedAt=trip.updated_at,
        myRole=role,
    )


class MemberUpsert(Input):
    role: StrictStr = Field(description="'editor' or 'viewer'")


class MemberOut(BaseModel):
    userId: str
    displayName: str
    role: Role
    addedAt: datetime


def member_out(p: Participant) -> MemberOut:
    return MemberOut(userId=p.user.id, displayName=p.user.display_name, role=p.role,
                     addedAt=p.added_at)


class UserOut(BaseModel):
    id: str
    displayName: str
