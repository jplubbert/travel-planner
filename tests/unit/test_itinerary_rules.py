"""US-04 read-time checks (simplified scheduling rules), as pure functions."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.domain.bookings import Booking, BookingStatus
from app.domain.itinerary import RULES, ItineraryItem, build_view
from app.domain.trips import Trip

LIS = timezone(timedelta(hours=1))
MAD = timezone(timedelta(hours=2))
NOW = datetime(2030, 6, 1, 12, tzinfo=UTC)
TRIP = Trip(uuid4(), "T", ("Lisbon", "Madrid"), date(2030, 6, 10), date(2030, 6, 18), 2,
            Decimal("3000.00"), 3, "ana", NOW, NOW)


def item(category, start, end, *, destination="Lisbon", title=None, travelers=2,
         available=True, valid_until=NOW + timedelta(minutes=15), position=0, origin=None):
    return ItineraryItem(
        id=uuid4(), trip_id=TRIP.id, position=position, offer_id=f"offer-{uuid4()}",
        provider_id="supplier-a", category=category, title=title or category,
        description="d", destination=destination, origin=origin, starts_at=start, ends_at=end,
        travelers=travelers, price=Decimal("10.00"), offer_revision="r1", available=available,
        valid_until=valid_until, quoted_at=NOW, added_by="ana", created_at=NOW,
        offer_snapshot={},
    )


def at(day, hour, tz=LIS, minute=0):
    return datetime(2030, 6, day, hour, minute, tzinfo=tz)


def stay(start_day, end_day, destination="Lisbon", tz=LIS):
    return item("accommodation", at(start_day, 15, tz), at(end_day, 11, tz),
                destination=destination)


def flags(view, it):
    return {f.code: f for v in view.items if v.item.id == it.id for f in v.flags}


def test_overlapping_activities_conflict_both_ways():
    a = item("activity", at(11, 10), at(11, 12), title="Tram")
    b = item("activity", at(11, 9), at(11, 17), title="Sintra")
    view = build_view(TRIP, [a, b], [], NOW)
    assert flags(view, a)["SCHEDULE_CONFLICT"].related_item_ids == (b.id,)
    assert flags(view, b)["SCHEDULE_CONFLICT"].related_item_ids == (a.id,)
    assert "'Sintra'" in flags(view, a)["SCHEDULE_CONFLICT"].message


def test_back_to_back_items_do_not_conflict():
    a = item("activity", at(11, 10), at(11, 12))
    b = item("transport", at(11, 12), at(11, 14))
    view = build_view(TRIP, [a, b], [], NOW)
    assert "SCHEDULE_CONFLICT" not in flags(view, a) | flags(view, b)


def test_overlap_uses_absolute_instants_across_offsets():
    # 11:00+01:00 == 12:00+02:00: same instant, so 11:30+01:00 overlaps 12:00-13:00+02:00
    a = item("transport", at(14, 11, LIS), at(14, 11, LIS, minute=59))
    b = item("activity", at(14, 12, MAD), at(14, 13, MAD))
    assert "SCHEDULE_CONFLICT" in flags(build_view(TRIP, [a, b], [], NOW), a)


def test_activities_during_a_stay_are_expected():
    hotel = stay(10, 14)
    tour = item("activity", at(11, 10), at(11, 12))
    view = build_view(TRIP, [hotel, tour], [], NOW)
    assert "SCHEDULE_CONFLICT" not in flags(view, hotel) | flags(view, tour)


def test_two_stays_on_the_same_night_conflict():
    a, b = stay(10, 14), stay(13, 16)
    assert "SCHEDULE_CONFLICT" in flags(build_view(TRIP, [a, b], [], NOW), a)


def test_outside_trip_dates_uses_the_local_date():
    # 23:30 local on the last day is 21:30Z: inside. 00:30 local the day after: outside.
    inside = item("activity", at(18, 22, MAD), at(18, 23, MAD, minute=30))
    outside = item("activity", at(18, 23, MAD), datetime(2030, 6, 19, 0, 30, tzinfo=MAD))
    early = item("activity", at(9, 10), at(9, 12))
    view = build_view(TRIP, [inside, outside, early], [], NOW)
    assert "OUTSIDE_TRIP_DATES" not in flags(view, inside)
    assert "OUTSIDE_TRIP_DATES" in flags(view, outside)
    assert "OUTSIDE_TRIP_DATES" in flags(view, early)


def test_expired_price_unless_confirmed_by_a_booking():
    expired = item("activity", at(11, 10), at(11, 12), valid_until=NOW)
    booked = item("activity", at(12, 10), at(12, 12), valid_until=NOW - timedelta(days=1))
    confirmed = Booking(uuid4(), TRIP.id, booked.id, booked.offer_id, "r1", Decimal("10.00"),
                        "ref", "key", "ana", BookingStatus.CONFIRMED, NOW, NOW,
                        provider_booking_id="b1", accepted_offer={},
                        accepted_price=Decimal("10.00"))
    view = build_view(TRIP, [expired, booked], [confirmed], NOW)
    assert "PRICE_EXPIRED" in flags(view, expired)  # at validUntil it is already expired
    assert "PRICE_EXPIRED" not in flags(view, booked)
    assert view.items[1].confirmed_booking == confirmed


def test_unavailable_and_party_mismatch_are_flagged():
    gone = item("activity", at(11, 10), at(11, 12), available=False)
    big = item("activity", at(12, 10), at(12, 12), travelers=4)
    view = build_view(TRIP, [gone, big], [], NOW)
    assert "OFFER_UNAVAILABLE" in flags(view, gone)
    assert "PARTY_SIZE_MISMATCH" in flags(view, big)


def test_missing_accommodation_nights_and_destinations():
    view = build_view(TRIP, [stay(10, 13)], [], NOW)  # Lisbon nights 10, 11, 12
    gaps = {(g.code, g.message) for g in view.missing}
    assert ("NO_ACCOMMODATION_FOR_DESTINATION", "No accommodation in Madrid.") in gaps
    assert ("NIGHTS_WITHOUT_ACCOMMODATION",
            "No accommodation for the nights of 2030-06-13 to 2030-06-17.") in gaps


def train(origin, destination, day=14):
    return item("transport", at(day, 8), at(day, 14, MAD), origin=origin,
                destination=destination)


def test_fully_covered_trip_has_no_gaps():
    view = build_view(TRIP, [stay(10, 14), train("lisbon", "MADRID"),
                             stay(14, 18, "madrid", MAD)], [], NOW)
    assert view.missing == ()


def transport_gaps(view):
    return [g.message for g in view.missing if g.code == "MISSING_TRANSPORT"]


def test_transport_between_consecutive_destinations_closes_the_gap():
    view = build_view(TRIP, [stay(10, 14), train("Lisbon", "Madrid")], [], NOW)
    assert transport_gaps(view) == []


def test_missing_transport_between_consecutive_destinations_is_a_gap():
    three = replace(TRIP, destinations=("Lisbon", "Madrid", "Paris"))
    view = build_view(three, [stay(10, 14)], [], NOW)
    assert transport_gaps(view) == ["Missing transport from Lisbon to Madrid.",
                                    "Missing transport from Madrid to Paris."]

    view = build_view(three, [stay(10, 14), train("Lisbon", "Madrid")], [], NOW)
    assert transport_gaps(view) == ["Missing transport from Madrid to Paris."]


def test_only_a_transport_in_that_direction_between_those_destinations_counts():
    others = [train("Madrid", "Lisbon"),  # wrong direction
              train("London", "Madrid"),  # another origin
              item("activity", at(14, 8), at(14, 12), origin="Lisbon",  # not transport
                   destination="Madrid")]
    view = build_view(TRIP, [stay(10, 14), *others], [], NOW)
    assert transport_gaps(view) == ["Missing transport from Lisbon to Madrid."]


def test_a_single_destination_never_needs_transport():
    single = replace(TRIP, destinations=("Lisbon",), ends_on=date(2030, 6, 14))
    view = build_view(single, [stay(10, 14)], [], NOW)
    assert view.missing == ()


def test_the_transport_rule_is_explained():
    assert any("consecutive destinations" in rule and "transport" in rule for rule in RULES)


def test_empty_itinerary_and_rules_are_reported():
    view = build_view(TRIP, [], [], NOW)
    assert [g.code for g in view.missing] == ["EMPTY_ITINERARY"]
    assert view.rules == RULES and len(RULES) >= 5


def test_items_are_ordered_by_position():
    a = item("activity", at(11, 10), at(11, 12), position=1)
    b = item("activity", at(12, 10), at(12, 12), position=0)
    assert [v.item.id for v in build_view(TRIP, [a, b], [], NOW).items] == [b.id, a.id]
