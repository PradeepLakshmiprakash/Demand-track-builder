"""A proactive, non-billable position becomes billable on a day the demand owner records.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("demands", sa.Column("billable_from", sa.Date(), nullable=True))
    op.add_column("demands", sa.Column("billable_marked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("demands", sa.Column("billable_marked_by", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_demands_billable_marked_by", "demands", "users", ["billable_marked_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_demands_billable_marked_by", "demands", type_="foreignkey")
    for col in ("billable_marked_by", "billable_marked_at", "billable_from"):
        op.drop_column("demands", col)
