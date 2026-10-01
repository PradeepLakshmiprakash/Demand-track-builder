"""Escalations for BCM sheet rows that no demand in the app is linked to.

An escalation now belongs to an account and may have no demand: it then carries the sheet row's
requisition ID, name and (when the originator is a known owner) who to copy.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES_OLD = (
    "not_submitted", "missing", "dropped", "incorrect", "aging", "rejection_limit", "panel_sla", "past_start",
)  # fmt: skip
TYPES_NEW = (*TYPES_OLD, "unlinked_row")


def _in(values: tuple[str, ...]) -> str:
    return f"type IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("escalations", sa.Column("account_id", sa.Integer(), nullable=True))
    op.execute("UPDATE escalations e SET account_id = d.account_id FROM demands d WHERE d.id = e.demand_id")
    op.alter_column("escalations", "account_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_escalations_account_id_accounts"), "escalations", "accounts", ["account_id"], ["id"]
    )
    op.create_index(op.f("ix_escalations_account_id"), "escalations", ["account_id"])
    op.alter_column("escalations", "demand_id", nullable=True)
    op.add_column("escalations", sa.Column("sheet_req_id", sa.String(20), nullable=True))
    op.add_column("escalations", sa.Column("sheet_name", sa.Text(), nullable=True))
    op.add_column("escalations", sa.Column("sheet_owner_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_escalations_sheet_owner_id_users"), "escalations", "users", ["sheet_owner_id"], ["id"]
    )
    op.create_check_constraint(
        op.f("ck_escalations_demand_or_row"), "escalations", "demand_id IS NOT NULL OR sheet_req_id IS NOT NULL"
    )
    op.drop_constraint(op.f("ck_escalations_type_valid"), "escalations", type_="check")
    op.create_check_constraint(op.f("ck_escalations_type_valid"), "escalations", _in(TYPES_NEW))
    op.create_index(
        "uq_escalations_open_per_row",
        "escalations",
        ["account_id", "sheet_req_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND sheet_req_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Sheet-row escalations have no demand to fall back on: they go.
    op.drop_index("uq_escalations_open_per_row", table_name="escalations")
    op.execute("DELETE FROM escalation_events WHERE escalation_id IN (SELECT id FROM escalations WHERE demand_id IS NULL)")
    op.execute("DELETE FROM escalations WHERE demand_id IS NULL")
    op.drop_constraint(op.f("ck_escalations_type_valid"), "escalations", type_="check")
    op.create_check_constraint(op.f("ck_escalations_type_valid"), "escalations", _in(TYPES_OLD))
    op.drop_constraint(op.f("ck_escalations_demand_or_row"), "escalations", type_="check")
    op.drop_constraint(op.f("fk_escalations_sheet_owner_id_users"), "escalations", type_="foreignkey")
    op.drop_column("escalations", "sheet_owner_id")
    op.drop_column("escalations", "sheet_name")
    op.drop_column("escalations", "sheet_req_id")
    op.alter_column("escalations", "demand_id", nullable=False)
    op.drop_index(op.f("ix_escalations_account_id"), table_name="escalations")
    op.drop_constraint(op.f("fk_escalations_account_id_accounts"), "escalations", type_="foreignkey")
    op.drop_column("escalations", "account_id")
