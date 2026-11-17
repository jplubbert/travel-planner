from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class TripChange:
    """One accepted change to a trip; ``version`` is the trip version it produced.
    Lets a participant whose edit conflicted see what happened since their version."""

    trip_id: UUID
    version: int
    actor_id: str
    # trip_created | trip_updated | item_added | item_removed | items_reordered |
    # item_quote_refreshed | history_start
    kind: str
    summary: str
    at: datetime
