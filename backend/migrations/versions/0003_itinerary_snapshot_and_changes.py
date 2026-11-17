"""itinerary offer snapshot and trip change history

- itinerary_items gains description, available and offer_snapshot (ADR 0004). Existing
  rows are backfilled from their columns before the NOT NULL constraints are applied;
  backfilled snapshots carry UTC offsets because the original local offsets were not
  stored before this migration.
- One itinerary item per offer and trip (unique index).
- trip_changes records one accepted change per trip version (ADR 0001 / US-06). Existing
  trips get a starting entry at their current version.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("itinerary_items", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("itinerary_items", sa.Column("available", sa.Boolean(), nullable=True))
    op.add_column("itinerary_items",
                  sa.Column("offer_snapshot", postgresql.JSONB(astext_type=sa.Text()),
                            nullable=True))
    op.execute("""
        UPDATE itinerary_items SET
            description = title,
            available = true,
            offer_snapshot = jsonb_strip_nulls(jsonb_build_object(
                'id', offer_id, 'providerId', provider_id, 'category', category,
                'title', title, 'description', title, 'destination', destination,
                'origin', origin,
                'startsAt', to_char(starts_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                'endsAt', to_char(ends_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                'travelers', travelers,
                'price', jsonb_build_object('amount', to_char(price, 'FM999999999990.00'),
                                            'currency', 'USD'),
                'revision', offer_revision, 'available', true,
                'validUntil', to_char(valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"'),
                'restrictions', restrictions))
    """)
    for column in ("description", "available", "offer_snapshot"):
        op.alter_column("itinerary_items", column, nullable=False)
    op.create_index("uq_itinerary_items_trip_offer", "itinerary_items", ["trip_id", "offer_id"],
                    unique=True)

    op.create_table(
        "trip_changes",
        sa.Column("trip_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"],
                                name=op.f("fk_trip_changes_actor_id_users")),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"],
                                name=op.f("fk_trip_changes_trip_id_trips"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trip_id", "version", name=op.f("pk_trip_changes")),
    )
    op.execute("""
        INSERT INTO trip_changes (trip_id, version, actor_id, kind, summary, at)
        SELECT id, version, created_by, 'history_start',
               'Change history starts at this version', updated_at
        FROM trips
    """)


def downgrade() -> None:
    op.drop_table("trip_changes")
    op.drop_index("uq_itinerary_items_trip_offer", table_name="itinerary_items")
    op.drop_column("itinerary_items", "offer_snapshot")
    op.drop_column("itinerary_items", "available")
    op.drop_column("itinerary_items", "description")
