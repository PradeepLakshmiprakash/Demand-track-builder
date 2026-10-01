"""Administrator role (app controls only, no demands) and requests to the Administrator.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES_OLD = ("demand_owner", "admin", "admin_team", "leadership", "interviewer")
SCOPES_OLD = ("own", "own_bu_read", "full", "assigned_interviews")
MATCH_OLD = (
    "(role IN ('admin', 'admin_team', 'leadership') AND visibility_scope = 'full')"
    " OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews')"
    " OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))"
)
MATCH_NEW = MATCH_OLD + " OR (role = 'administrator' AND visibility_scope = 'app_controls')"


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _swap(name: str, rule: str) -> None:
    op.drop_constraint(op.f(f"ck_user_accounts_{name}"), "user_accounts", type_="check")
    op.create_check_constraint(op.f(f"ck_user_accounts_{name}"), "user_accounts", rule)


def upgrade() -> None:
    _swap("role_valid", _in("role", (*ROLES_OLD, "administrator")))
    _swap("scope_valid", _in("visibility_scope", (*SCOPES_OLD, "app_controls")))
    _swap("scope_matches_role", MATCH_NEW)
    op.create_table(
        "admin_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), server_default="open", nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("handled_by", sa.Integer(), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('access', 'settings', 'rate_card', 'other')", name=op.f("ck_admin_requests_kind_valid")),
        sa.CheckConstraint("status IN ('open', 'done', 'declined')", name=op.f("ck_admin_requests_status_valid")),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_admin_requests_account_id_accounts")),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], name=op.f("fk_admin_requests_requested_by_users")),
        sa.ForeignKeyConstraint(["handled_by"], ["users.id"], name=op.f("fk_admin_requests_handled_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_requests")),
    )
    op.create_index(op.f("ix_admin_requests_account_id"), "admin_requests", ["account_id"])


def downgrade() -> None:
    # Fails while anyone is an Administrator: change their role first, never silently.
    op.drop_index(op.f("ix_admin_requests_account_id"), table_name="admin_requests")
    op.drop_table("admin_requests")
    _swap("scope_matches_role", MATCH_OLD)
    _swap("scope_valid", _in("visibility_scope", SCOPES_OLD))
    _swap("role_valid", _in("role", ROLES_OLD))
