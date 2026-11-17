"""ADR 0003: local booking state machine."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.bookings import ALLOWED, TERMINAL, Booking, BookingStatus
from app.domain.errors import InvalidTransition

S = BookingStatus
NOW = datetime(2030, 6, 1, 12, tzinfo=UTC)
OFFER = {"id": "offer-x-p2", "price": {"amount": "100.00", "currency": "USD"}}


def booking(status: BookingStatus = S.DRAFT) -> Booking:
    return Booking(
        id=uuid4(), trip_id=uuid4(), itinerary_item_id=None, offer_id="offer-x-p2",
        expected_revision="r1", expected_price=Decimal("100.00"), client_reference="ref",
        idempotency_key="key", requested_by="ana", status=status, created_at=NOW,
        updated_at=NOW,
    )


def accept(b: Booking, status: str, provider_id: str = "booking-1") -> None:
    b.record_provider_booking(
        provider_booking_id=provider_id, provider_status=status, offer=OFFER,
        price=Decimal("100.00"), failure={"code": "X", "message": "m"} if status == "failed"
        else None, now=NOW,
    )


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_terminal_states_never_change(terminal):
    assert ALLOWED[terminal] == frozenset()
    b = booking(terminal)
    for target in BookingStatus:
        with pytest.raises(InvalidTransition):
            b._move(target, NOW)


def test_unknown_only_resolves_to_provider_outcomes_or_rejected():
    assert ALLOWED[S.UNKNOWN] == {S.PENDING, S.CONFIRMED, S.FAILED, S.REJECTED}


def test_unknown_cannot_go_back_to_draft_or_submitted():
    b = booking(S.UNKNOWN)
    for target in (S.DRAFT, S.SUBMITTED, S.UNKNOWN):
        with pytest.raises(InvalidTransition):
            b._move(target, NOW)


def test_pending_never_degrades_to_unknown():
    with pytest.raises(InvalidTransition):
        booking(S.PENDING).record_unknown({"code": "PROVIDER_TIMEOUT"}, NOW)


def test_happy_path_keeps_accepted_snapshot():
    b = booking()
    b.submit(NOW)
    assert b.status == S.SUBMITTED and b.submitted_at == NOW
    accept(b, "pending")
    accept(b, "pending")  # polling again: no change, no error
    accept(b, "confirmed")
    assert b.status == S.CONFIRMED
    assert b.provider_booking_id == "booking-1" and b.accepted_offer == OFFER
    assert b.accepted_price == Decimal("100.00")


def test_failed_is_an_accepted_booking_with_failure():
    b = booking(S.SUBMITTED)
    accept(b, "failed")
    assert b.status == S.FAILED and b.provider_booking_id == "booking-1"
    assert (b.failure_code, b.failure_message) == ("X", "m")


def test_rejected_has_no_provider_booking():
    b = booking(S.SUBMITTED)
    b.record_rejection("OFFER_CHANGED", "terms changed", NOW)
    assert b.status == S.REJECTED and b.provider_booking_id is None


def test_not_accepted_returns_to_draft_and_can_be_resubmitted():
    b = booking(S.SUBMITTED)
    b.record_not_accepted({"code": "PROVIDER_UNAVAILABLE", "providerRequestId": "r1"}, NOW)
    assert b.status == S.DRAFT and b.last_error["code"] == "PROVIDER_UNAVAILABLE"
    b.submit(NOW)
    assert b.status == S.SUBMITTED and b.last_error is None


def test_lost_response_becomes_unknown_then_reconciles():
    b = booking(S.SUBMITTED)
    b.record_unknown({"code": "PROVIDER_TIMEOUT"}, NOW)
    assert b.status == S.UNKNOWN
    accept(b, "confirmed")
    assert b.status == S.CONFIRMED


def test_provider_returning_a_different_booking_is_refused():
    b = booking(S.SUBMITTED)
    accept(b, "pending", "booking-1")
    with pytest.raises(InvalidTransition):
        accept(b, "confirmed", "booking-2")


def test_draft_cannot_jump_to_an_outcome():
    for target in (S.PENDING, S.CONFIRMED, S.FAILED, S.REJECTED, S.UNKNOWN):
        with pytest.raises(InvalidTransition):
            booking(S.DRAFT)._move(target, NOW)


def test_accepted_snapshot_is_never_overwritten_by_later_provider_data():
    b = booking(S.SUBMITTED)
    accept(b, "pending")
    b.record_provider_booking(
        provider_booking_id="booking-1", provider_status="confirmed",
        offer={"id": "offer-x-p2", "price": {"amount": "999.00", "currency": "USD"}},
        price=Decimal("999.00"), failure=None, now=NOW)
    assert b.status == S.CONFIRMED
    assert b.accepted_offer == OFFER and b.accepted_price == Decimal("100.00")
