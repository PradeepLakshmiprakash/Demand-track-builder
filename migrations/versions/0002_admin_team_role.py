"""Admin team role: the admin demand owner's team, full account, does the manual admin work.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_OLD = "role IN ('demand_owner', 'admin', 'leadership', 'interviewer')"
ROLE_NEW = "role IN ('demand_owner', 'admin', 'admin_team', 'leadership', 'interviewer')"
SCOPE_TAIL = (
    " OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews')"
    " OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))"
)
SCOPE_OLD = "(role IN ('admin', 'leadership') AND visibility_scope = 'full')" + SCOPE_TAIL
SCOPE_NEW = "(role IN ('admin', 'admin_team', 'leadership') AND visibility_scope = 'full')" + SCOPE_TAIL


def _swap(role: str, scope: str) -> None:
    op.drop_constraint(op.f("ck_users_role_valid"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_scope_matches_role"), "users", type_="check")
    op.create_check_constraint(op.f("ck_users_role_valid"), "users", role)
    op.create_check_constraint(op.f("ck_users_scope_matches_role"), "users", scope)


def upgrade() -> None:
    _swap(ROLE_NEW, SCOPE_NEW)


def downgrade() -> None:
    # Refuses (constraint violation) while admin team users exist: reassign them first, never delete history.
    _swap(ROLE_OLD, SCOPE_OLD)
