"""trips.preferences: the group's free-text preferences and restrictions (US-11)

Existing trips get "" (no preferences), the same value a trip created without them has.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("preferences", sa.Text(), server_default="",
                                     nullable=False))
    op.create_check_constraint(op.f("ck_trips_preferences_length"), "trips",
                               "char_length(preferences) <= 500")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_trips_preferences_length"), "trips", type_="check")
    op.drop_column("trips", "preferences")
