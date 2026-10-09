"""Practices and grades as tables that demands and rates must match, day-by-day figures, a link from a
re-raised demand to the one it replaces, and a save counter on accounts.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    for table, width in (("practices", 40), ("grades", 10)):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("account_id", sa.Integer(),
                      sa.ForeignKey("accounts.id", name=f"fk_{table}_account_id_accounts"), nullable=False),
            sa.Column("name", sa.String(width), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("position", sa.SmallInteger(), nullable=False),
            sa.UniqueConstraint("account_id", "name", name=f"uq_{table}_account_name"),
        )  # fmt: skip
        key = table  # the settings key has the same name as the table
        # the account's list, in its order
        op.execute(
            f"""
            INSERT INTO {table} (account_id, name, active, position)
            SELECT a.id, left(v.value, {width}), true, v.ordinality - 1
            FROM accounts a,
                 jsonb_array_elements_text(COALESCE(a.config -> '{key}', '[]'::jsonb)) WITH ORDINALITY AS v
            WHERE btrim(v.value) <> ''
            ON CONFLICT (account_id, name) DO NOTHING
            """
        )
    # anything already on a demand or a rate that isn't on the list: kept, as inactive
    for table, column in (("practices", "practice"), ("grades", "grade")):
        for source in ("demands", "rate_cards"):
            op.execute(
                f"""
                INSERT INTO {table} (account_id, name, active, position)
                SELECT DISTINCT account_id, {column}, false, 999 FROM {source} WHERE {column} IS NOT NULL
                ON CONFLICT (account_id, name) DO NOTHING
                """
            )
            op.create_foreign_key(
                f"fk_{source}_{column}", source, table, ["account_id", column], ["account_id", "name"],
                onupdate="CASCADE",
            )  # fmt: skip

    op.create_table(
        "daily_figures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(),
                  sa.ForeignKey("accounts.id", name="fk_daily_figures_account_id_accounts"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("late_positions", sa.Integer(), nullable=False),
        sa.Column("revenue_lost", sa.Numeric(14, 2), nullable=False),
        sa.Column("nb_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("unbilled", sa.Numeric(14, 2), nullable=False),
        sa.Column("escalations_open", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "day", name="uq_daily_figures_account_day"),
    )  # fmt: skip
    op.add_column(
        "demands",
        sa.Column("replaces_demand_id", sa.Integer(),
                  sa.ForeignKey("demands.id", name="fk_demands_replaces_demand_id_demands")),
    )  # fmt: skip
    op.add_column("accounts", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("accounts", "version")
    op.drop_column("demands", "replaces_demand_id")
    op.drop_table("daily_figures")
    for source, column in (("demands", "practice"), ("demands", "grade"), ("rate_cards", "practice"),
                           ("rate_cards", "grade")):  # fmt: skip
        op.drop_constraint(f"fk_{source}_{column}", source, type_="foreignkey")
    op.drop_table("grades")
    op.drop_table("practices")
