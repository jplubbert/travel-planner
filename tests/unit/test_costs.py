"""US-05 cost breakdown and rounding rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.bookings import Booking, BookingStatus
from app.domain.costs import percent, summarize
from app.domain.itinerary import ItineraryItem

NOW = datetime(2030, 6, 1, 12, tzinfo=UTC)
TRIP_ID = uuid4()


def item(price, position=0):
    return ItineraryItem(
        id=uuid4(), trip_id=TRIP_ID, position=position, offer_id=f"offer-{uuid4()}",
        provider_id="supplier-a", category="activity", title=f"Item {position}",
        description="d", destination="Lisbon", origin=None, starts_at=NOW,
        ends_at=NOW + timedelta(hours=1), travelers=2, price=Decimal(price),
        offer_revision="r1", available=True, valid_until=NOW, quoted_at=NOW,
        added_by="ana", created_at=NOW, offer_snapshot={},
    )


def booking(it, status, accepted=None, created=NOW):
    return Booking(uuid4(), TRIP_ID, it.id, it.offer_id, "r1", it.price, f"ref-{uuid4()}",
                   f"key-{uuid4()}", "ana", status, created, created,
                   provider_booking_id="b" if accepted else None,
                   accepted_offer={} if accepted else None,
                   accepted_price=Decimal(accepted) if accepted else None)


def test_cents_add_up_exactly_without_float_error():
    items = [item("0.10", 0), item("0.20", 1), item("0.30", 2)]
    s = summarize(items, [], Decimal("1.00"))
    assert s.total == Decimal("0.60")  # 0.1 + 0.2 + 0.3 in floats is 0.6000000000000001
    assert str(s.total) == "0.60" and str(s.remaining) == "0.40"


def test_many_odd_cent_prices_sum_reproducibly():
    items = [item("39.99", n) for n in range(7)] + [item("89.90", 7)]
    s = summarize(items, [], Decimal("500.00"))
    assert s.total == Decimal("369.83")
    assert s.used_percent == Decimal("74.0")  # 73.966 -> 74.0 (half-up, 1 decimal)


@pytest.mark.parametrize("part,whole,expected", [
    ("1", "8", "12.5"),  # exactly 12.5
    ("1", "6", "16.7"),  # 16.666...
    ("1", "16", "6.3"),  # 6.25 -> half-up gives 6.3 (banker's rounding would give 6.2)
    ("3", "16", "18.8"),  # 18.75 -> 18.8
    ("0", "10", "0.0"),
    ("15", "10", "150.0"),
])
def test_used_percent_rounds_half_up_to_one_decimal(part, whole, expected):
    assert percent(Decimal(part), Decimal(whole)) == Decimal(expected)


def test_zero_budget_has_no_percentage_but_is_over_budget():
    s = summarize([item("10.00")], [], Decimal("0.00"))
    assert s.used_percent is None and s.over_budget and s.remaining == Decimal("-10.00")


def test_exactly_on_budget_is_not_over():
    s = summarize([item("100.00")], [], Decimal("100.00"))
    assert not s.over_budget and s.remaining == Decimal("0.00")
    assert s.used_percent == Decimal("100.0")


def test_confirmed_booking_uses_accepted_price_not_current_quote():
    hotel = item("700.00")  # quote has since changed
    s = summarize([hotel], [booking(hotel, BookingStatus.CONFIRMED, accepted="670.00")],
                  Decimal("1000.00"))
    line = s.lines[0]
    assert line.basis == "confirmed" and line.amount == Decimal("670.00")
    assert s.confirmed_total == Decimal("670.00") and s.estimated_total == Decimal("0.00")


@pytest.mark.parametrize("status", [BookingStatus.PENDING, BookingStatus.UNKNOWN,
                                    BookingStatus.SUBMITTED])
def test_in_progress_booking_is_still_an_estimate(status):
    it = item("50.00")
    accepted = "50.00" if status == BookingStatus.PENDING else None
    line = summarize([it], [booking(it, status, accepted)], Decimal("100")).lines[0]
    assert line.basis == "estimate" and status.value in line.note


@pytest.mark.parametrize("status", [BookingStatus.FAILED, BookingStatus.REJECTED])
def test_failed_or_rejected_booking_leaves_an_estimate(status):
    it = item("50.00")
    accepted = "50.00" if status == BookingStatus.FAILED else None
    line = summarize([it], [booking(it, status, accepted)], Decimal("100")).lines[0]
    assert line.basis == "estimate" and line.amount == Decimal("50.00")


def test_confirmed_wins_over_a_later_failed_attempt():
    it = item("50.00")
    ok = booking(it, BookingStatus.CONFIRMED, accepted="45.00", created=NOW)
    later = booking(it, BookingStatus.REJECTED, created=NOW + timedelta(minutes=1))
    line = summarize([it], [ok, later], Decimal("100")).lines[0]
    assert line.basis == "confirmed" and line.amount == Decimal("45.00")


def test_mixed_totals_and_breakdown_order():
    a, b, c = item("100.00", 2), item("200.00", 0), item("300.00", 1)
    s = summarize([a, b, c], [booking(c, BookingStatus.CONFIRMED, accepted="250.00")],
                  Decimal("500.00"))
    assert [line.item_id for line in s.lines] == [b.id, c.id, a.id]
    assert (s.estimated_total, s.confirmed_total, s.total) == (
        Decimal("300.00"), Decimal("250.00"), Decimal("550.00"))
    assert s.over_budget and s.remaining == Decimal("-50.00")
    assert s.used_percent == Decimal("110.0")
