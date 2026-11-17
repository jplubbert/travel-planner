"""Relational schema. Changes here require an Alembic migration (a test compares both)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(12, 2)
TZ = DateTime(timezone=True)
# Python None must be SQL NULL, not the JSON value null: constraints and the bookings
# trigger test "IS NULL" (by default SQLAlchemy stores None as JSON null).
NullableJSON = JSONB(none_as_null=True)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)


class TripRow(Base):
    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("starts_on <= ends_on", name="dates_ordered"),
        CheckConstraint("travelers BETWEEN 1 AND 20", name="travelers_range"),
        CheckConstraint("budget >= 0", name="budget_non_negative"),
        CheckConstraint("currency = 'USD'", name="currency_usd"),
        CheckConstraint("cardinality(destinations) >= 1", name="has_destination"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    destinations: Mapped[list[str]] = mapped_column(ARRAY(Text))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    travelers: Mapped[int] = mapped_column(Integer)
    budget: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3), server_default="USD")
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


ROLES = ("organizer", "editor", "viewer")


class TripChangeRow(Base):
    __tablename__ = "trip_changes"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
    # Primary key (trip_id, version): at most one accepted change per version.
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TZ)


class TripMemberRow(Base):
    __tablename__ = "trip_members"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="role_valid"),
        # Exactly one organizer per trip.
        Index("uq_trip_members_one_organizer", "trip_id", unique=True,
              postgresql_where=text("role = 'organizer'")),
        Index("ix_trip_members_user_id", "user_id"),
    )

    trip_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    added_at: Mapped[datetime] = mapped_column(TZ)


CATEGORIES = ("transport", "accommodation", "activity")


class ItineraryItemRow(Base):
    __tablename__ = "itinerary_items"
    __table_args__ = (
        CheckConstraint(_in("category", CATEGORIES), name="category_valid"),
        CheckConstraint("starts_at < ends_at", name="schedule_ordered"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        Index("ix_itinerary_items_trip_id", "trip_id"),
        Index("uq_itinerary_items_trip_offer", "trip_id", "offer_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    offer_id: Mapped[str] = mapped_column(String(128))
    provider_id: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    destination: Mapped[str] = mapped_column(Text)
    origin: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(TZ)
    ends_at: Mapped[datetime] = mapped_column(TZ)
    travelers: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Money)
    offer_revision: Mapped[str] = mapped_column(Text)
    available: Mapped[bool] = mapped_column(Boolean)
    valid_until: Mapped[datetime] = mapped_column(TZ)
    quoted_at: Mapped[datetime] = mapped_column(TZ)
    restrictions: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    # The quote exactly as the provider returned it (ADR 0004); source of local offsets.
    offer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    added_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(TZ)


BOOKING_STATUSES = ("draft", "submitted", "unknown", "pending", "confirmed", "failed", "rejected")


class BookingRow(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(_in("status", BOOKING_STATUSES), name="status_valid"),
        CheckConstraint("expected_price >= 0", name="price_non_negative"),
        # A provider-backed state must carry the provider booking and its snapshot.
        CheckConstraint(
            "status NOT IN ('pending', 'confirmed', 'failed') "
            "OR (provider_booking_id IS NOT NULL AND accepted_offer IS NOT NULL "
            "AND accepted_price IS NOT NULL)",
            name="accepted_has_snapshot",
        ),
        Index("ix_bookings_trip_id", "trip_id"),
        Index("ix_bookings_status", "status"),
        # US-08: at most one active attempt per itinerary item (double clicks, races).
        Index("uq_bookings_one_active_per_item", "itinerary_item_id", unique=True,
              postgresql_where=text(
                  "status IN ('draft', 'submitted', 'unknown', 'pending', 'confirmed')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # RESTRICT: booking history outlives itinerary edits and is never cascaded away.
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="RESTRICT"))
    itinerary_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("itinerary_items.id", ondelete="SET NULL"))
    offer_id: Mapped[str] = mapped_column(String(128))
    expected_revision: Mapped[str] = mapped_column(Text)
    expected_price: Mapped[Decimal] = mapped_column(Money)
    client_reference: Mapped[str] = mapped_column(String(128), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16))
    submitted_at: Mapped[datetime | None] = mapped_column(TZ)
    provider_booking_id: Mapped[str | None] = mapped_column(String(128))
    accepted_offer: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)
    accepted_price: Mapped[Decimal | None] = mapped_column(Money)
    failure_code: Mapped[str | None] = mapped_column(Text)
    failure_message: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)
    rejection_terms: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)


DOCUMENT_STATUSES = ("waiting", "in_progress", "complete", "failed")


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(_in("status", DOCUMENT_STATUSES), name="status_valid"),
        # "complete" is only valid with a stored, verifiable file.
        CheckConstraint(
            "status <> 'complete' OR (storage_key IS NOT NULL AND content_sha256 IS NOT NULL "
            "AND size_bytes IS NOT NULL AND completed_at IS NOT NULL)",
            name="complete_has_file",
        ),
        Index("ix_documents_trip_id", "trip_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trips.id", ondelete="RESTRICT"))
    trip_version: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)
    completed_at: Mapped[datetime | None] = mapped_column(TZ)


JOB_STATUSES = ("queued", "running", "succeeded", "failed")


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status_valid"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
        # One active job per dedupe key; finished jobs do not block new ones.
        Index("uq_jobs_active_dedupe_key", "dedupe_key", unique=True,
              postgresql_where=text("status IN ('queued', 'running')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    run_after: Mapped[datetime] = mapped_column(TZ)
    locked_until: Mapped[datetime | None] = mapped_column(TZ)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZ)
    updated_at: Mapped[datetime] = mapped_column(TZ)
