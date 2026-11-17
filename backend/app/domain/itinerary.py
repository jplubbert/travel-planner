from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

Category = Literal["transport", "accommodation", "activity"]


@dataclass(frozen=True)
class ItineraryItem:
    """An option selected into the itinerary, with the quote seen when it was added.

    The price is an estimate until a booking confirms it (US-05); ``valid_until`` lets the
    application flag expired prices (US-04).
    """

    id: UUID
    trip_id: UUID
    position: int
    offer_id: str
    provider_id: str
    category: Category
    title: str
    destination: str
    origin: str | None
    starts_at: datetime
    ends_at: datetime
    travelers: int
    price: Decimal  # USD, whole option for the whole party
    offer_revision: str
    valid_until: datetime
    quoted_at: datetime
    added_by: str
    created_at: datetime
    restrictions: tuple[str, ...] = field(default=())
