"""Escalation engine: audit events, mailed level, BU delivery heads, "no further action".

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-24 08:11:31.364999

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACTIONS_OLD = "action IN ('resubmit', 'extend', 'close')"
ACTIONS_NEW = "action IN ('resubmit', 'extend', 'close', 'no_action')"


def upgrade() -> None:
    op.create_table('escalation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('escalation_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint("kind IN ('opened', 'notified', 'promoted', 'extended', 'resolved')", name=op.f('ck_escalation_events_kind_valid')),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_escalation_events_actor_id_users')),
    sa.ForeignKeyConstraint(['escalation_id'], ['escalations.id'], name=op.f('fk_escalation_events_escalation_id_escalations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_escalation_events'))
    )
    op.create_index(op.f('ix_escalation_events_escalation_id'), 'escalation_events', ['escalation_id'], unique=False)
    op.add_column('business_units', sa.Column('delivery_head_name', sa.String(length=120), nullable=True))
    op.add_column('business_units', sa.Column('delivery_head_email', sa.String(length=254), nullable=True))
    op.add_column('escalations', sa.Column('notified_level', sa.SmallInteger(), server_default='0', nullable=False))
    # Escalations that exist already were mailed when they opened (seed/Phase 3), so don't mail them again.
    op.execute("UPDATE escalations SET notified_level = level")
    op.drop_constraint(op.f("ck_escalations_action_valid"), "escalations", type_="check")
    op.create_check_constraint(op.f("ck_escalations_action_valid"), "escalations", f"action IS NULL OR {ACTIONS_NEW}")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_escalations_action_valid"), "escalations", type_="check")
    op.create_check_constraint(op.f("ck_escalations_action_valid"), "escalations", f"action IS NULL OR {ACTIONS_OLD}")
    op.drop_column('escalations', 'notified_level')
    op.drop_column('business_units', 'delivery_head_email')
    op.drop_column('business_units', 'delivery_head_name')
    op.drop_index(op.f('ix_escalation_events_escalation_id'), table_name='escalation_events')
    op.drop_table('escalation_events')
