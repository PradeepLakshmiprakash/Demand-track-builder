"""DP sheet rows carry what reconciliation needs: matching fields, outcome, suggestions.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOMES = "'matched', 'prefix', 'confirmed', 'suggested', 'unmatched', 'conflict', 'duplicate', 'invalid'"


def upgrade() -> None:
    op.add_column("excel_imports", sa.Column("file_sha256", sa.String(length=64), nullable=True))
    op.add_column(
        "excel_imports",
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(op.f("ix_excel_imports_file_sha256"), "excel_imports", ["file_sha256"])

    for name, type_ in [
        ("originator", sa.String(length=160)),
        ("practice", sa.String(length=40)),
        ("grade", sa.String(length=10)),
        ("region", sa.String(length=10)),
        ("start_date", sa.Date()),
        ("gettalent_req_id", sa.String(length=40)),
        ("candidate_details", sa.Text()),
        ("note", sa.Text()),
    ]:
        op.add_column("excel_rows", sa.Column(name, type_, nullable=True))
    op.add_column(
        "excel_rows", sa.Column("outcome", sa.String(length=12), nullable=False, server_default="unmatched")
    )
    op.add_column(
        "excel_rows",
        sa.Column("suggestions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("excel_rows", sa.Column("matched_by", sa.Integer(), nullable=True))
    op.create_foreign_key(op.f("fk_excel_rows_matched_by_users"), "excel_rows", "users", ["matched_by"], ["id"])
    op.create_check_constraint(op.f("ck_excel_rows_outcome_valid"), "excel_rows", f"outcome IN ({OUTCOMES})")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_excel_rows_outcome_valid"), "excel_rows", type_="check")
    op.drop_constraint(op.f("fk_excel_rows_matched_by_users"), "excel_rows", type_="foreignkey")
    for name in (
        "matched_by", "suggestions", "outcome", "note", "candidate_details", "gettalent_req_id",
        "start_date", "region", "grade", "practice", "originator",
    ):  # fmt: skip
        op.drop_column("excel_rows", name)
    op.drop_index(op.f("ix_excel_imports_file_sha256"), table_name="excel_imports")
    op.drop_column("excel_imports", "summary")
    op.drop_column("excel_imports", "file_sha256")
