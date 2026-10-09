"""When the owner set the joining date, so a later BCM sheet can replace it and an earlier one can't.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("demands", sa.Column("expected_doj_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("demands", "expected_doj_at")
