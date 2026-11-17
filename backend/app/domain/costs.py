"""US-05: itemized costs, totals and budget comparison (USD, Decimal only).

Rounding rules (also returned to clients as ROUNDING):
- Provider prices and accepted booking prices are already exact cents; they are summed
  exactly with Decimal (no floats, no intermediate rounding).
- Every money value is normalized to 2 decimals with ROUND_HALF_UP before it is returned,
  which is a no-op for cent amounts and protects against non-cent inputs.
- The budget percentage is total / budget * 100, rounded ROUND_HALF_UP to 1 decimal;
  it is omitted when the budget is 0.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from app.domain.bookings import Booking, BookingStatus
from app.domain.itinerary import ItineraryItem, bookings_by_item
from app.domain.money import round_usd

ROUNDING = (
    "Amounts are exact USD cents added with decimal arithmetic (no floating point and no "
    "intermediate rounding). Each amount is shown with 2 decimals, rounded half-up. "
    "usedPercent = total / budget x 100, rounded half-up to 1 decimal."
)

Basis = Literal["estimate", "confirmed"]

IN_PROGRESS = {BookingStatus.SUBMITTED, BookingStatus.UNKNOWN, BookingStatus.PENDING}


@dataclass(frozen=True)
class CostLine:
    item_id: UUID
    title: str
    category: str
    amount: Decimal
    basis: Basis
    booking_id: UUID | None
    booking_status: BookingStatus | None
    note: str


@dataclass(frozen=True)
class CostSummary:
    lines: tuple[CostLine, ...]
    estimated_total: Decimal
    confirmed_total: Decimal
    total: Decimal
    budget: Decimal
    remaining: Decimal  # negative when over budget
    over_budget: bool
    used_percent: Decimal | None


def percent(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole == 0:
        return None
    return (part / whole * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _line(item: ItineraryItem, confirmed: Booking | None, latest: Booking | None) -> CostLine:
    if confirmed is not None and confirmed.accepted_price is not None:
        return CostLine(item.id, item.title, item.category, round_usd(confirmed.accepted_price),
                        "confirmed", confirmed.id, confirmed.status,
                        "Price accepted when the booking was confirmed; later provider price "
                        "changes do not affect it.")
    if latest is not None and latest.status in IN_PROGRESS:
        note = (f"Booking is {latest.status}; this is still the quoted estimate, not a "
                "confirmed price.")
    else:
        note = f"Quoted estimate, valid until {item.valid_until.isoformat()}."
    return CostLine(item.id, item.title, item.category, round_usd(item.price), "estimate",
                    latest.id if latest else None, latest.status if latest else None, note)


def summarize(items: list[ItineraryItem], bookings: list[Booking], budget: Decimal) -> CostSummary:
    booked = bookings_by_item(bookings)
    lines = tuple(_line(i, *booked.get(i.id, (None, None)))
                  for i in sorted(items, key=lambda i: i.position))
    estimated = sum((line.amount for line in lines if line.basis == "estimate"), Decimal("0"))
    confirmed = sum((line.amount for line in lines if line.basis == "confirmed"), Decimal("0"))
    total = estimated + confirmed
    return CostSummary(
        lines=lines, estimated_total=round_usd(estimated), confirmed_total=round_usd(confirmed),
        total=round_usd(total), budget=round_usd(budget), remaining=round_usd(budget - total),
        over_budget=total > budget, used_percent=percent(total, budget),
    )
