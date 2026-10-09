"""From offer to first billable day: the pre-joining checklist, candidates who did not join, why billing
has not started, and two escalation triggers.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("not_submitted", "missing", "dropped", "incorrect", "aging", "rejection_limit", "panel_sla",
       "past_start", "unlinked_row")  # fmt: skip
NEW = (*OLD, "prejoin_overdue", "not_billing")


def _types(values: tuple[str, ...]) -> str:
    return "type IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "onboarding_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("demand_id", sa.Integer(), sa.ForeignKey("demands.id", ondelete="CASCADE",
                  name="fk_onboarding_items_demand_id_demands"), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("owner_kind", sa.String(10), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_onboarding_items_owner_id_users")),
        sa.Column("outside_label", sa.String(80)),
        sa.Column("due_days_before", sa.SmallInteger(), nullable=False),
        sa.Column("escalate", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_onboarding_items_updated_by_users")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('not_started', 'in_progress', 'done', 'blocked', 'not_needed')",
                           name="ck_onboarding_items_status_valid"),
        sa.CheckConstraint("owner_kind IN ('owner', 'person', 'gtd_team', 'outside')",
                           name="ck_onboarding_items_owner_kind_valid"),
    )  # fmt: skip
    op.create_index("ix_onboarding_items_demand", "onboarding_items", ["demand_id"])
    op.create_table(
        "candidate_exits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", name="fk_candidate_exits_account_id_accounts"),
                  nullable=False),
        sa.Column("demand_id", sa.Integer(), sa.ForeignKey("demands.id", name="fk_candidate_exits_demand_id_demands"),
                  nullable=False),
        sa.Column("candidate_id", sa.Integer(),
                  sa.ForeignKey("candidates.id", name="fk_candidate_exits_candidate_id_candidates")),
        sa.Column("candidate_name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id", name="fk_candidate_exits_recorded_by_users")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('declined', 'dropped_out', 'no_show', 'withdrawn')",
                           name="ck_candidate_exits_kind_valid"),
    )  # fmt: skip
    op.create_index("ix_candidate_exits_account_on", "candidate_exits", ["account_id", "on_date"])
    op.create_index("ix_candidate_exits_demand_id", "candidate_exits", ["demand_id"])
    op.add_column("demands", sa.Column("not_billing_reason", sa.String(120)))
    op.drop_constraint("type_valid", "escalations", type_="check")
    op.create_check_constraint("type_valid", "escalations", _types(NEW))


def downgrade() -> None:
    op.execute("DELETE FROM escalations WHERE type IN ('prejoin_overdue', 'not_billing')")
    op.drop_constraint("type_valid", "escalations", type_="check")
    op.create_check_constraint("type_valid", "escalations", _types(OLD))
    op.drop_column("demands", "not_billing_reason")
    op.drop_table("candidate_exits")
    op.drop_table("onboarding_items")
