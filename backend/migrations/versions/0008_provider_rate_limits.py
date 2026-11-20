"""provider_rate_limits: when each provider may be called again (shared 429 Retry-After)

One row per provider, created on its first 429. No rows for existing installations.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_rate_limits",
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_provider_rate_limits")),
    )


def downgrade() -> None:
    op.drop_table("provider_rate_limits")
