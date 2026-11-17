"""seed test users

Test identities for the demo (the PRD does not require an identity system). Requests
identify the acting user with the X-User-Id header.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USERS = [
    ("ana", "Ana"),
    ("beto", "Beto"),
    ("carla", "Carla"),
    ("dani", "Dani"),
]

users = sa.table("users", sa.column("id", sa.String), sa.column("display_name", sa.Text))


def upgrade() -> None:
    op.bulk_insert(users, [{"id": uid, "display_name": name} for uid, name in USERS])


def downgrade() -> None:
    op.execute(users.delete().where(users.c.id.in_([uid for uid, _ in USERS])))
