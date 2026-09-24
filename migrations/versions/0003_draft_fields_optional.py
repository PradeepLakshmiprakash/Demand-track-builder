"""Draft demands may lack practice and grade; anything past draft must have both.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE = "status = 'draft' OR (practice IS NOT NULL AND grade IS NOT NULL)"


def upgrade() -> None:
    op.alter_column("demands", "practice", existing_type=sa.VARCHAR(length=40), nullable=True)
    op.alter_column("demands", "grade", existing_type=sa.VARCHAR(length=10), nullable=True)
    op.create_check_constraint(op.f("ck_demands_submitted_is_complete"), "demands", RULE)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_demands_submitted_is_complete"), "demands", type_="check")
    # Fails while incomplete drafts exist: complete or remove them first, never silently.
    op.alter_column("demands", "grade", existing_type=sa.VARCHAR(length=10), nullable=False)
    op.alter_column("demands", "practice", existing_type=sa.VARCHAR(length=40), nullable=False)
