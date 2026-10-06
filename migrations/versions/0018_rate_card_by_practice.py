"""The rate card is cost per hour by practice and grade: supply channel and region leave it.

Where a practice and grade had several rates (one per channel or region), the series with the highest
rate in force is kept, so no cost is understated; the others are removed. Offers already priced keep
the figures they were priced on.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM rate_cards r USING (
            SELECT account_id, grade, coalesce(practice, '') AS p, region, channel,
                   row_number() OVER (
                       PARTITION BY account_id, grade, coalesce(practice, '')
                       ORDER BY max(cost_rate) FILTER (WHERE effective_to IS NULL) DESC NULLS LAST,
                                max(cost_rate) DESC, region, channel
                   ) AS rn
            FROM rate_cards
            GROUP BY account_id, grade, coalesce(practice, ''), region, channel
        ) k
        WHERE r.account_id = k.account_id AND r.grade = k.grade AND coalesce(r.practice, '') = k.p
          AND r.region = k.region AND r.channel = k.channel AND k.rn > 1
        """
    )
    op.drop_index("ix_rate_cards_lookup", table_name="rate_cards")
    op.drop_column("rate_cards", "channel")
    op.drop_column("rate_cards", "region")
    op.create_index("ix_rate_cards_lookup", "rate_cards", ["account_id", "grade", "practice"])


def downgrade() -> None:
    op.drop_index("ix_rate_cards_lookup", table_name="rate_cards")
    op.add_column("rate_cards", sa.Column("region", sa.String(10), nullable=False, server_default="US"))
    op.add_column("rate_cards", sa.Column("channel", sa.String(40), nullable=False, server_default="any"))
    op.create_index("ix_rate_cards_lookup", "rate_cards", ["account_id", "grade", "practice", "region", "channel"])
