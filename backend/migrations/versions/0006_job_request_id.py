"""jobs.request_id: correlation id of the request that created the job

Nullable: jobs created by the worker's own sweeps (and every job created before this
revision) have no originating request.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("request_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "request_id")
