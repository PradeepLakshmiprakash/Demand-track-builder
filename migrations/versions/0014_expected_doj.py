"""The demand owner records the expected date of joining once the offer is accepted.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("demands", sa.Column("expected_doj", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("demands", "expected_doj")
