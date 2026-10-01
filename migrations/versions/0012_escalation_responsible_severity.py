"""Escalations: a responsible person and a severity; overdue reminders; "revise the start date".

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS_OLD = ("resubmit", "extend", "close", "no_action", "return")
ACTIONS_NEW = (*ACTIONS_OLD, "new_start")


def _action_rule(values: tuple[str, ...]) -> str:
    return f"action IS NULL OR action IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("escalations", sa.Column("severity", sa.String(6), server_default="medium", nullable=False))
    op.add_column(
        "escalations", sa.Column("responsible", sa.String(14), server_default="gtd_team", nullable=False)
    )
    op.add_column("escalations", sa.Column("last_reminded_on", sa.Date(), nullable=True))
    # Existing escalations take the default rule for their trigger.
    op.execute(
        "UPDATE escalations SET responsible = CASE"
        " WHEN type IN ('not_submitted', 'missing') THEN 'gtd_team'"
        " WHEN type = 'panel_sla' THEN 'interviewer' ELSE 'demand_owner' END,"
        " severity = CASE WHEN type IN ('dropped', 'past_start') THEN 'high'"
        " WHEN type IN ('aging', 'panel_sla') THEN 'low' ELSE 'medium' END"
    )
    op.create_check_constraint(
        op.f("ck_escalations_severity_valid"), "escalations", "severity IN ('high', 'medium', 'low')"
    )
    op.create_check_constraint(
        op.f("ck_escalations_responsible_valid"),
        "escalations",
        "responsible IN ('gtd_team', 'demand_owner', 'interviewer')",
    )
    op.drop_constraint(op.f("ck_escalations_action_valid"), "escalations", type_="check")
    op.create_check_constraint(op.f("ck_escalations_action_valid"), "escalations", _action_rule(ACTIONS_NEW))


def downgrade() -> None:
    # Fails while an escalation was resolved with "revise the start date": never silently.
    op.drop_constraint(op.f("ck_escalations_action_valid"), "escalations", type_="check")
    op.create_check_constraint(op.f("ck_escalations_action_valid"), "escalations", _action_rule(ACTIONS_OLD))
    op.drop_constraint(op.f("ck_escalations_responsible_valid"), "escalations", type_="check")
    op.drop_constraint(op.f("ck_escalations_severity_valid"), "escalations", type_="check")
    op.drop_column("escalations", "last_reminded_on")
    op.drop_column("escalations", "responsible")
    op.drop_column("escalations", "severity")
