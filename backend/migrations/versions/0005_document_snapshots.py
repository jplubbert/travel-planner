"""document snapshots (US-09/US-10)

- documents gain the immutable snapshot they render (snapshot, snapshot_at,
  snapshot_sha256). There are no documents before this revision (nothing generated
  them), so the NOT NULL columns are added directly; a database with rows would need
  a backfill and fails loudly here instead of inventing content.
- One waiting/in-progress document per trip version (partial unique index).
- A trigger keeps a document's snapshot, version, trip and requester immutable.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUARD_FUNCTION = """
CREATE FUNCTION documents_guard() RETURNS trigger AS $$
BEGIN
    IF NEW.snapshot IS DISTINCT FROM OLD.snapshot
       OR NEW.snapshot_at IS DISTINCT FROM OLD.snapshot_at
       OR NEW.snapshot_sha256 IS DISTINCT FROM OLD.snapshot_sha256
       OR NEW.trip_version IS DISTINCT FROM OLD.trip_version
       OR NEW.trip_id IS DISTINCT FROM OLD.trip_id
       OR NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
        RAISE EXCEPTION 'snapshot of document % is immutable', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

GUARD_BINDING = """
CREATE TRIGGER documents_guard BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION documents_guard()
"""


def upgrade() -> None:
    op.add_column("documents", sa.Column(
        "snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False))
    op.add_column("documents", sa.Column(
        "snapshot_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column("documents", sa.Column("snapshot_sha256", sa.String(length=64),
                                         nullable=False))
    op.create_index("uq_documents_one_active_per_version", "documents",
                    ["trip_id", "trip_version"], unique=True,
                    postgresql_where=sa.text("status IN ('waiting', 'in_progress')"))
    op.execute(GUARD_FUNCTION)
    op.execute(GUARD_BINDING)


def downgrade() -> None:
    op.execute("DROP TRIGGER documents_guard ON documents")
    op.execute("DROP FUNCTION documents_guard()")
    op.drop_index("uq_documents_one_active_per_version", table_name="documents")
    op.drop_column("documents", "snapshot_sha256")
    op.drop_column("documents", "snapshot_at")
    op.drop_column("documents", "snapshot")
