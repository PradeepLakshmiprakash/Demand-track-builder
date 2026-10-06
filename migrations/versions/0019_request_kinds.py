"""Requests to the Administrator come from every role, about more kinds of change.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW = "kind IN ('access', 'special', 'settings', 'escalation', 'rate_card', 'profile', 'data_fix', 'other')"
OLD = "kind IN ('access', 'settings', 'rate_card', 'other')"


def upgrade() -> None:
    op.drop_constraint("kind_valid", "admin_requests", type_="check")
    op.create_check_constraint("kind_valid", "admin_requests", NEW)


def downgrade() -> None:
    op.execute("UPDATE admin_requests SET kind = 'other' WHERE kind NOT IN ('access', 'settings', 'rate_card')")
    op.drop_constraint("kind_valid", "admin_requests", type_="check")
    op.create_check_constraint("kind_valid", "admin_requests", OLD)
