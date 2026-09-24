"""Rate card history and offer approvals that can wait unpriced until both rates are known.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-24 08:25:06.563900

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('offer_approvals', sa.Column('demand_id', sa.Integer(), nullable=False))
    op.add_column('offer_approvals', sa.Column('channel', sa.String(length=40), nullable=True))
    op.add_column('offer_approvals', sa.Column('blocked_reason', sa.Text(), nullable=True))
    op.add_column('offer_approvals', sa.Column('priced_on', sa.Date(), nullable=True))
    op.alter_column('offer_approvals', 'bill_rate',
               existing_type=sa.NUMERIC(precision=10, scale=2),
               nullable=True)
    op.alter_column('offer_approvals', 'cost_rate',
               existing_type=sa.NUMERIC(precision=10, scale=2),
               nullable=True)
    op.alter_column('offer_approvals', 'margin_pct',
               existing_type=sa.NUMERIC(precision=6, scale=2),
               nullable=True)
    op.alter_column('offer_approvals', 'route',
               existing_type=sa.VARCHAR(length=12),
               nullable=True)
    op.create_index(op.f('ix_offer_approvals_demand_id'), 'offer_approvals', ['demand_id'], unique=False)
    op.create_foreign_key(op.f('fk_offer_approvals_demand_id_demands'), 'offer_approvals', 'demands', ['demand_id'], ['id'])
    op.add_column('rate_cards', sa.Column('created_by', sa.Integer(), nullable=True))
    op.add_column('rate_cards', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.create_foreign_key(op.f('fk_rate_cards_created_by_users'), 'rate_cards', 'users', ['created_by'], ['id'])
    op.create_check_constraint(op.f("ck_rate_cards_cost_positive"), "rate_cards", "cost_rate >= 0")
    op.drop_constraint(op.f("ck_offer_approvals_route_valid"), "offer_approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_offer_approvals_route_valid"), "offer_approvals", "route IS NULL OR route IN ('admin', 'leadership')"
    )
    op.create_check_constraint(
        op.f("ck_offer_approvals_priced_means_routed"), "offer_approvals", "(route IS NULL) = (margin_pct IS NULL)"
    )
    op.create_check_constraint(
        op.f("ck_offer_approvals_decided_needs_approver"),
        "offer_approvals",
        "decision IS NULL OR (route IS NOT NULL AND approver_id IS NOT NULL AND decided_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_offer_approvals_decided_needs_approver"), "offer_approvals", type_="check")
    op.drop_constraint(op.f("ck_offer_approvals_priced_means_routed"), "offer_approvals", type_="check")
    op.drop_constraint(op.f("ck_offer_approvals_route_valid"), "offer_approvals", type_="check")
    op.create_check_constraint(
        op.f("ck_offer_approvals_route_valid"), "offer_approvals", "route IN ('admin', 'leadership')"
    )
    op.drop_constraint(op.f("ck_rate_cards_cost_positive"), "rate_cards", type_="check")
    # Fails while unpriced approvals exist: price or remove them first, never silently.
    op.drop_constraint(op.f('fk_rate_cards_created_by_users'), 'rate_cards', type_='foreignkey')
    op.drop_column('rate_cards', 'created_at')
    op.drop_column('rate_cards', 'created_by')
    op.drop_constraint(op.f('fk_offer_approvals_demand_id_demands'), 'offer_approvals', type_='foreignkey')
    op.drop_index(op.f('ix_offer_approvals_demand_id'), table_name='offer_approvals')
    op.alter_column('offer_approvals', 'route',
               existing_type=sa.VARCHAR(length=12),
               nullable=False)
    op.alter_column('offer_approvals', 'margin_pct',
               existing_type=sa.NUMERIC(precision=6, scale=2),
               nullable=False)
    op.alter_column('offer_approvals', 'cost_rate',
               existing_type=sa.NUMERIC(precision=10, scale=2),
               nullable=False)
    op.alter_column('offer_approvals', 'bill_rate',
               existing_type=sa.NUMERIC(precision=10, scale=2),
               nullable=False)
    op.drop_column('offer_approvals', 'priced_on')
    op.drop_column('offer_approvals', 'blocked_reason')
    op.drop_column('offer_approvals', 'channel')
    op.drop_column('offer_approvals', 'demand_id')
