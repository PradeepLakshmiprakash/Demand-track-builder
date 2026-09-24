"""Second account: a person's role, visibility and level belong to their membership in an account, so
one person can work in several accounts (with a different role in each). Adds the platform admin flag.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("demand_owner", "admin", "admin_team", "leadership", "interviewer")
SCOPES = ("own", "own_bu_read", "full", "assigned_interviews")
ROLE_RULE = f"role IN ({', '.join(repr(r) for r in ROLES)})"
SCOPE_RULE = f"visibility_scope IN ({', '.join(repr(s) for s in SCOPES)})"
MATCH_RULE = (
    "(role IN ('admin', 'admin_team', 'leadership') AND visibility_scope = 'full')"
    " OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews')"
    " OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))"
)
CHECKS = (("role_valid", ROLE_RULE), ("scope_valid", SCOPE_RULE), ("scope_matches_role", MATCH_RULE))


def _columns(table: str) -> None:
    op.add_column(table, sa.Column("role", sa.String(20), nullable=True))
    op.add_column(table, sa.Column("level", sa.String(10), nullable=True))
    op.add_column(table, sa.Column("visibility_scope", sa.String(24), nullable=True))


def _finish(table: str) -> None:
    op.alter_column(table, "role", nullable=False)
    op.alter_column(table, "visibility_scope", nullable=False)
    for name, rule in CHECKS:
        op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, rule)


def _drop(table: str) -> None:
    for name, _ in CHECKS:
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    for col in ("role", "level", "visibility_scope"):
        op.drop_column(table, col)


def upgrade() -> None:
    _columns("user_accounts")
    op.add_column("user_accounts", sa.Column("active", sa.Boolean(), server_default="true", nullable=False))
    op.execute(
        "UPDATE user_accounts ua SET role = u.role, level = u.level, visibility_scope = u.visibility_scope,"
        " active = u.active FROM users u WHERE u.id = ua.user_id"
    )
    _finish("user_accounts")
    _drop("users")
    op.execute("UPDATE users SET active = true")  # signing in at all; access per account is on the membership
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    # A person's role comes back from their first membership; roles in other accounts are lost.
    op.drop_column("users", "is_platform_admin")
    _columns("users")
    op.execute(
        "UPDATE users u SET role = ua.role, level = ua.level, visibility_scope = ua.visibility_scope,"
        " active = u.active AND ua.active"
        " FROM (SELECT DISTINCT ON (user_id) * FROM user_accounts ORDER BY user_id, account_id) ua"
        " WHERE ua.user_id = u.id"
    )
    op.execute("UPDATE users SET role = 'demand_owner', visibility_scope = 'own' WHERE role IS NULL")
    _finish("users")
    op.drop_column("user_accounts", "active")
    _drop("user_accounts")
