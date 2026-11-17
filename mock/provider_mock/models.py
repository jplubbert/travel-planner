"""Request models mirroring the contract schemas (additionalProperties: false, strict types)."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

AMOUNT_PATTERN = r"^(0|[1-9][0-9]*)\.[0-9]{2}$"

NonEmpty = Annotated[str, StringConstraints(strict=True, min_length=1)]
Str128 = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
Str200 = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
IsoDate = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
Amount = Annotated[str, StringConstraints(strict=True, pattern=AMOUNT_PATTERN)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        # The contract never allows null; an optional property is either a value or absent.
        if isinstance(data, dict):
            nulls = sorted(k for k, v in data.items() if v is None)
            if nulls:
                raise ValueError(f"null is not allowed for: {', '.join(nulls)}")
        return data


class Money(ContractModel):
    amount: Amount
    currency: Literal["USD"]


class SearchRequest(ContractModel):
    category: Literal["transport", "accommodation", "activity"]
    destination: Str200
    origin: Str200 | None = None
    startsOn: IsoDate
    endsOn: IsoDate
    travelers: Annotated[int, Field(strict=True, ge=1, le=20)]
    providerId: Str128 | None = None

    @model_validator(mode="after")
    def _check_semantics(self) -> "SearchRequest":
        try:
            starts, ends = date.fromisoformat(self.startsOn), date.fromisoformat(self.endsOn)
        except ValueError as exc:
            raise ValueError("startsOn and endsOn must be valid calendar dates") from exc
        if starts > ends:
            raise ValueError("startsOn must be on or before endsOn")
        if self.category == "transport" and self.origin is None:
            raise ValueError("origin is required for transport searches")
        return self

    @property
    def starts_on(self) -> date:
        return date.fromisoformat(self.startsOn)

    @property
    def ends_on(self) -> date:
        return date.fromisoformat(self.endsOn)


class CreateBookingRequest(ContractModel):
    clientReference: Str128
    offerId: Str128
    expectedOfferRevision: NonEmpty
    expectedPrice: Money


# --- Control interface (outside the public contract) ---


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplierId: str
    mode: Literal["normal", "unavailable", "timeout", "rate_limited"] = "normal"
    delaySeconds: Annotated[float, Field(ge=0, le=300)] = 10.0
    retryAfterSeconds: Annotated[int, Field(ge=1, le=3600)] = 2
    bookingOutcome: Literal["confirmed", "pending", "failed"] | None = None


class ClockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now: datetime | None = None
    advanceSeconds: float | None = None
    frozen: bool = True


class FailureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: NonEmpty
    message: NonEmpty


class SettleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "failed"]
    failure: FailureBody | None = None


class OfferTermsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    price: Amount | None = None
    available: bool | None = None
