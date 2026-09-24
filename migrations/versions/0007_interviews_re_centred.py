"""Interviews re-centred on mapping candidates to requisitions: candidates and interviews can exist
before the requisition, interviewer or time is known; L2 requests; Karat fields; interviewer alerts.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24 09:12:53.031918

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHECKS = [
    ("ck_candidates_source_valid", "source IN ('sheet', 'manual', 'karat')"),
    ("ck_interviews_status_valid", "status IN ('requested', 'declined', 'open', 'scheduled', 'completed')"),
    ("ck_interviews_source_valid", "source IN ('app', 'karat')"),
    ("ck_interviews_completed_has_outcome",
     "status <> 'completed' OR (outcome IS NOT NULL AND submitted_at IS NOT NULL)"),
    ("ck_interviews_scheduled_has_interviewer", "status <> 'scheduled' OR interviewer_id IS NOT NULL"),
]


def upgrade() -> None:
    op.add_column('candidates', sa.Column('account_id', sa.Integer(), nullable=True))
    op.execute("UPDATE candidates c SET account_id = d.account_id FROM demands d WHERE d.id = c.demand_id")
    op.alter_column('candidates', 'account_id', existing_type=sa.Integer(), nullable=False)
    op.add_column('candidates', sa.Column('source', sa.String(length=10), server_default='sheet', nullable=False))
    op.add_column('candidates', sa.Column('cv_path', sa.Text(), nullable=True))
    op.alter_column('candidates', 'demand_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.create_index(op.f('ix_candidates_account_id'), 'candidates', ['account_id'], unique=False)
    op.create_index('uq_candidates_demand_name', 'candidates', ['demand_id', sa.literal_column('lower(name)')], unique=True, postgresql_where=sa.text('demand_id IS NOT NULL'))
    op.create_foreign_key(op.f('fk_candidates_account_id_accounts'), 'candidates', 'accounts', ['account_id'], ['id'])
    op.add_column('demands', sa.Column('interviewers_alerted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('interviews', sa.Column('status', sa.String(length=12), server_default='scheduled', nullable=False))
    op.add_column('interviews', sa.Column('source', sa.String(length=10), server_default='app', nullable=False))
    op.add_column('interviews', sa.Column('needs_next_round', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('interviews', sa.Column('requested_by', sa.Integer(), nullable=True))
    op.add_column('interviews', sa.Column('request_note', sa.Text(), nullable=True))
    op.add_column('interviews', sa.Column('decided_by', sa.Integer(), nullable=True))
    op.add_column('interviews', sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('interviews', sa.Column('decision_note', sa.Text(), nullable=True))
    op.add_column('interviews', sa.Column('external_ref', sa.String(length=80), nullable=True))
    op.add_column('interviews', sa.Column('report_url', sa.Text(), nullable=True))
    op.alter_column('interviews', 'demand_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.alter_column('interviews', 'interviewer_id',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.alter_column('interviews', 'feedback_token',
               existing_type=sa.VARCHAR(length=64),
               nullable=True)
    op.create_index(op.f('ix_interviews_candidate_id'), 'interviews', ['candidate_id'], unique=False)
    op.create_foreign_key(op.f('fk_interviews_requested_by_users'), 'interviews', 'users', ['requested_by'], ['id'])
    op.create_foreign_key(op.f('fk_interviews_decided_by_users'), 'interviews', 'users', ['decided_by'], ['id'])
    for name, rule in CHECKS:
        op.create_check_constraint(op.f(name), name.split("_")[1], rule)


def downgrade() -> None:
    for name, _ in CHECKS:
        op.drop_constraint(op.f(name), name.split("_")[1], type_="check")
    # Fails while candidates or interviews without a requisition exist: map them first, never silently.
    op.drop_constraint(op.f('fk_interviews_decided_by_users'), 'interviews', type_='foreignkey')
    op.drop_constraint(op.f('fk_interviews_requested_by_users'), 'interviews', type_='foreignkey')
    op.drop_index(op.f('ix_interviews_candidate_id'), table_name='interviews')
    op.alter_column('interviews', 'feedback_token',
               existing_type=sa.VARCHAR(length=64),
               nullable=False)
    op.alter_column('interviews', 'interviewer_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.alter_column('interviews', 'demand_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.drop_column('interviews', 'report_url')
    op.drop_column('interviews', 'external_ref')
    op.drop_column('interviews', 'decision_note')
    op.drop_column('interviews', 'decided_at')
    op.drop_column('interviews', 'decided_by')
    op.drop_column('interviews', 'request_note')
    op.drop_column('interviews', 'requested_by')
    op.drop_column('interviews', 'needs_next_round')
    op.drop_column('interviews', 'source')
    op.drop_column('interviews', 'status')
    op.drop_column('demands', 'interviewers_alerted_at')
    op.drop_constraint(op.f('fk_candidates_account_id_accounts'), 'candidates', type_='foreignkey')
    op.drop_index('uq_candidates_demand_name', table_name='candidates', postgresql_where=sa.text('demand_id IS NOT NULL'))
    op.drop_index(op.f('ix_candidates_account_id'), table_name='candidates')
    op.alter_column('candidates', 'demand_id',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.drop_column('candidates', 'cv_path')
    op.drop_column('candidates', 'source')
    op.drop_column('candidates', 'account_id')
