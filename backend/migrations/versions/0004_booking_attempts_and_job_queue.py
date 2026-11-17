"""booking attempts (US-07/US-08) and job queue

- bookings: at most one active attempt per itinerary item (partial unique index);
  last_error becomes structured JSON; rejection_terms keeps the provider's terms at a
  rejection. Existing text errors are wrapped as {"code": "LEGACY", "message": ...}.
- A trigger makes provider-accepted data immutable once set and terminal statuses final
  (ADR 0003 rules 1 and 6), whatever code path writes the row.
- Python None stored as JSON null by earlier code is normalized to SQL NULL.
- jobs: dedupe_key is unique only among active (queued/running) jobs, so a booking can be
  synchronized again after an earlier job finished.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "status IN ('draft', 'submitted', 'unknown', 'pending', 'confirmed')"

TRIGGER_FUNCTION = """
CREATE FUNCTION bookings_guard() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('confirmed', 'failed', 'rejected') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'booking % is % and cannot change to %', OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF (OLD.provider_booking_id IS NOT NULL
            AND NEW.provider_booking_id IS DISTINCT FROM OLD.provider_booking_id)
       OR (OLD.accepted_offer IS NOT NULL AND NEW.accepted_offer IS DISTINCT FROM OLD.accepted_offer)
       OR (OLD.accepted_price IS NOT NULL AND NEW.accepted_price IS DISTINCT FROM OLD.accepted_price)
       OR NEW.client_reference <> OLD.client_reference
       OR NEW.idempotency_key <> OLD.idempotency_key THEN
        RAISE EXCEPTION 'accepted data of booking % is immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

TRIGGER_BINDING = """
CREATE TRIGGER bookings_guard BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION bookings_guard()
"""


def upgrade() -> None:
    op.alter_column(
        "bookings", "last_error", type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="CASE WHEN last_error IS NULL THEN NULL ELSE "
                         "jsonb_build_object('code', 'LEGACY', 'message', last_error) END",
    )
    op.add_column("bookings", sa.Column("rejection_terms", postgresql.JSONB(astext_type=sa.Text()),
                                        nullable=True))
    # Earlier code stored Python None as the JSON value null; make it SQL NULL so
    # "IS NULL" checks (constraint accepted_has_snapshot, the trigger below) are truthful.
    op.execute("UPDATE bookings SET accepted_offer = NULL WHERE accepted_offer = 'null'::jsonb")
    op.execute("UPDATE bookings SET last_error = NULL WHERE last_error = 'null'::jsonb")
    op.create_index("uq_bookings_one_active_per_item", "bookings", ["itinerary_item_id"],
                    unique=True, postgresql_where=sa.text(ACTIVE))
    op.execute(TRIGGER_FUNCTION)
    op.execute(TRIGGER_BINDING)

    op.drop_constraint("uq_jobs_dedupe_key", "jobs", type_="unique")
    op.create_index("uq_jobs_active_dedupe_key", "jobs", ["dedupe_key"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index("uq_jobs_active_dedupe_key", table_name="jobs")
    op.create_unique_constraint("uq_jobs_dedupe_key", "jobs", ["dedupe_key"])
    op.execute("DROP TRIGGER bookings_guard ON bookings")
    op.execute("DROP FUNCTION bookings_guard()")
    op.drop_index("uq_bookings_one_active_per_item", table_name="bookings")
    op.drop_column("bookings", "rejection_terms")
    op.alter_column("bookings", "last_error", type_=sa.Text(),
                    postgresql_using="last_error->>'message'")
