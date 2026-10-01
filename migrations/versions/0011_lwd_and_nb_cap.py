"""Replacement demands record the leaver's last working day; business units get a non-billable cap.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("demands", sa.Column("lwd", sa.Date(), nullable=True))
    op.add_column("business_units", sa.Column("nb_cap", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_business_units_nb_cap_positive"), "business_units", "nb_cap IS NULL OR nb_cap >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_business_units_nb_cap_positive"), "business_units", type_="check")
    op.drop_column("business_units", "nb_cap")
    op.drop_column("demands", "lwd")
