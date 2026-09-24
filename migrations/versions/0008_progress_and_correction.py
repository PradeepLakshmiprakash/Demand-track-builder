"""Interview progress moves the demand (Interviewing, Selected by panel); demands can be sent back to the
owner for correction (Returned); old requisition IDs in the DP sheet are ignored (superseded).

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES_OLD = (
    "draft", "submitted", "notified", "sent_to_gtd", "linked", "missing", "dropped", "incorrect",
    "coverage_required", "profiles_with_client", "offer_in_process", "offer_in_market", "staffed",
    "cancelled", "closed",
)  # fmt: skip
STATUSES_NEW = STATUSES_OLD + ("returned", "interviewing", "panel_selected")
ACTIONS_OLD = ("resubmit", "extend", "close", "no_action")
ACTIONS_NEW = ACTIONS_OLD + ("return",)
OUTCOMES_OLD = ("matched", "prefix", "confirmed", "suggested", "unmatched", "conflict", "duplicate", "invalid")
OUTCOMES_NEW = OUTCOMES_OLD + ("superseded",)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _swap(table: str, name: str, rule: str) -> None:
    op.drop_constraint(op.f(name), table, type_="check")
    op.create_check_constraint(op.f(name), table, rule)


def upgrade() -> None:
    op.add_column(
        "demands", sa.Column("client_interview_required", sa.Boolean(), server_default="true", nullable=False)
    )
    _swap("demands", "ck_demands_status_valid", _in("status", STATUSES_NEW))
    _swap("escalations", "ck_escalations_action_valid", f"action IS NULL OR {_in('action', ACTIONS_NEW)}")
    _swap("excel_rows", "ck_excel_rows_outcome_valid", _in("outcome", OUTCOMES_NEW))


def downgrade() -> None:
    # Fails while rows use the new values: move them first, never silently.
    _swap("excel_rows", "ck_excel_rows_outcome_valid", _in("outcome", OUTCOMES_OLD))
    _swap("escalations", "ck_escalations_action_valid", f"action IS NULL OR {_in('action', ACTIONS_OLD)}")
    _swap("demands", "ck_demands_status_valid", _in("status", STATUSES_OLD))
    op.drop_column("demands", "client_interview_required")
