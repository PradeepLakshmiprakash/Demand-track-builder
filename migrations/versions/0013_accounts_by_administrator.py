"""Client accounts are added by the Administrator role; the separate platform-admin flag goes.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "is_platform_admin")


def downgrade() -> None:
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean(), server_default="false", nullable=False))
    # Every Administrator managed accounts under the role rule; give them the flag back.
    op.execute(
        "UPDATE users SET is_platform_admin = true WHERE id IN"
        " (SELECT user_id FROM user_accounts WHERE role = 'administrator')"
    )
