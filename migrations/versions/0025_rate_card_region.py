"""A rate can be for one region. Amounts stay in USD.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rate_cards", sa.Column("region", sa.String(10)))


def downgrade() -> None:
    op.execute("DELETE FROM rate_cards WHERE region IS NOT NULL")
    op.drop_column("rate_cards", "region")
