"""The job description typed on the demand form, and when the owner was asked to confirm billing.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("demands", sa.Column("jd_text", sa.Text()))
    op.add_column("demands", sa.Column("billing_asked_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("demands", "billing_asked_at")
    op.drop_column("demands", "jd_text")
