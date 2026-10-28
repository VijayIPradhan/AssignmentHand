"""shared rate limiter

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-28 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rate_limit_hits_key_at", "rate_limit_hits", ["key", "at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_key_at", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
