"""The client's decision on a candidate, recorded by the demand owner (the client has no access).

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("client_outcome", sa.String(10), nullable=True))
    op.add_column("candidates", sa.Column("client_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("candidates", sa.Column("client_decided_by", sa.Integer(), nullable=True))
    op.add_column("candidates", sa.Column("client_note", sa.Text(), nullable=True))
    op.create_foreign_key("fk_candidates_client_decided_by", "candidates", "users", ["client_decided_by"], ["id"])
    op.create_check_constraint(
        "ck_candidates_client_outcome_valid", "candidates", "client_outcome IN ('select', 'reject')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_candidates_client_outcome_valid", "candidates", type_="check")
    op.drop_constraint("fk_candidates_client_decided_by", "candidates", type_="foreignkey")
    for col in ("client_note", "client_decided_by", "client_decided_at", "client_outcome"):
        op.drop_column("candidates", col)
