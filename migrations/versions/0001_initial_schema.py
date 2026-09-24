"""Initial schema: every table from techstack.md section 3.

Revision ID: 0001
Revises: 
Create Date: 2026-09-23 22:51:08.536670

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres owns the app reference (DM-000142); demands.app_ref defaults from this sequence.
    op.execute(sa.schema.CreateSequence(sa.Sequence("demand_ref_seq")))
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('grace_days', sa.Integer(), nullable=False),
    sa.Column('l1_sla_days', sa.Integer(), nullable=False),
    sa.Column('l2_sla_days', sa.Integer(), nullable=False),
    sa.Column('panel_timer_hours', sa.Integer(), nullable=False),
    sa.Column('aging_days', sa.Integer(), nullable=False),
    sa.Column('rejection_limit', sa.Integer(), nullable=False),
    sa.Column('margin_threshold', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('mail_time', sa.Time(), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
    sa.UniqueConstraint('name', name=op.f('uq_accounts_name'))
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=True),
    sa.Column('visibility_scope', sa.String(length=24), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(role IN ('admin', 'leadership') AND visibility_scope = 'full') OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews') OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))", name=op.f('ck_users_scope_matches_role')),
    sa.CheckConstraint("role IN ('demand_owner', 'admin', 'leadership', 'interviewer')", name=op.f('ck_users_role_valid')),
    sa.CheckConstraint("visibility_scope IN ('own', 'own_bu_read', 'full', 'assigned_interviews')", name=op.f('ck_users_scope_valid')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_users_created_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index('uq_users_email_lower', 'users', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('business_units',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_business_units_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_business_units')),
    sa.UniqueConstraint('account_id', 'name', name='uq_business_units_account_name')
    )
    op.create_table('excel_imports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('file_path', sa.Text(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('sheet_date', sa.Date(), nullable=True),
    sa.Column('uploaded_by', sa.Integer(), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_excel_imports_account_id_accounts')),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_excel_imports_uploaded_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_excel_imports'))
    )
    op.create_table('interviewer_profiles',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('practices', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('skills', postgresql.ARRAY(sa.String(length=60)), nullable=False),
    sa.Column('max_grade', sa.String(length=10), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_interviewer_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_interviewer_profiles'))
    )
    op.create_table('notification_batches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('demand_ids', postgresql.ARRAY(sa.Integer()), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_notification_batches_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_batches'))
    )
    op.create_table('rate_cards',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('grade', sa.String(length=10), nullable=False),
    sa.Column('practice', sa.String(length=40), nullable=True),
    sa.Column('region', sa.String(length=10), nullable=False),
    sa.Column('channel', sa.String(length=40), nullable=False),
    sa.Column('cost_rate', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.CheckConstraint('effective_to IS NULL OR effective_to >= effective_from', name=op.f('ck_rate_cards_dates_ordered')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_rate_cards_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rate_cards'))
    )
    op.create_index('ix_rate_cards_lookup', 'rate_cards', ['account_id', 'grade', 'practice', 'region', 'channel'], unique=False)
    op.create_table('user_accounts',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_user_accounts_account_id_accounts')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_accounts_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'account_id', name=op.f('pk_user_accounts'))
    )
    op.create_table('user_practices',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('practice', sa.String(length=40), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_practices_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'practice', name=op.f('pk_user_practices'))
    )
    op.create_table('demands',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('app_ref', sa.String(length=12), server_default=sa.text("'DM-' || lpad(nextval('demand_ref_seq')::text, 6, '0')"), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('bu_id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('practice', sa.String(length=40), nullable=False),
    sa.Column('grade', sa.String(length=10), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('position_type', sa.String(length=20), nullable=False),
    sa.Column('replaced_resource', sa.String(length=120), nullable=True),
    sa.Column('primary_skills', postgresql.ARRAY(sa.String(length=60)), nullable=False),
    sa.Column('secondary_skills', postgresql.ARRAY(sa.String(length=60)), nullable=False),
    sa.Column('exp_min', sa.Integer(), nullable=True),
    sa.Column('exp_max', sa.Integer(), nullable=True),
    sa.Column('client_rate', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('region', sa.String(length=10), nullable=True),
    sa.Column('location', sa.String(length=80), nullable=True),
    sa.Column('work_mode', sa.String(length=20), nullable=True),
    sa.Column('hiring_manager', sa.String(length=120), nullable=True),
    sa.Column('jd_path', sa.Text(), nullable=True),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'submitted', 'notified', 'sent_to_gtd', 'linked', 'missing', 'dropped', 'incorrect', 'coverage_required', 'profiles_with_client', 'offer_in_process', 'offer_in_market', 'staffed', 'cancelled', 'closed')", name=op.f('ck_demands_status_valid')),
    sa.CheckConstraint('client_rate IS NULL OR client_rate >= 0', name=op.f('ck_demands_rate_positive')),
    sa.CheckConstraint('exp_min IS NULL OR exp_max IS NULL OR exp_min <= exp_max', name=op.f('ck_demands_exp_range')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_demands_account_id_accounts')),
    sa.ForeignKeyConstraint(['bu_id'], ['business_units.id'], name=op.f('fk_demands_bu_id_business_units')),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name=op.f('fk_demands_owner_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_demands')),
    sa.UniqueConstraint('app_ref', name=op.f('uq_demands_app_ref'))
    )
    op.create_index('ix_demands_account_status', 'demands', ['account_id', 'status'], unique=False)
    op.create_index('ix_demands_owner', 'demands', ['owner_id'], unique=False)
    op.create_table('user_business_units',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('bu_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['bu_id'], ['business_units.id'], name=op.f('fk_user_business_units_bu_id_business_units')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_business_units_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'bu_id', name=op.f('pk_user_business_units'))
    )
    op.create_table('candidates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('demand_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('channel', sa.String(length=40), nullable=True),
    sa.Column('current_stage', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['demand_id'], ['demands.id'], name=op.f('fk_candidates_demand_id_demands')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates'))
    )
    op.create_index(op.f('ix_candidates_demand_id'), 'candidates', ['demand_id'], unique=False)
    op.create_table('escalations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('demand_id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.String(length=80), nullable=True),
    sa.Column('action', sa.String(length=10), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('resolved_by', sa.Integer(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("action IS NULL OR action IN ('resubmit', 'extend', 'close')", name=op.f('ck_escalations_action_valid')),
    sa.CheckConstraint("status = 'open' OR (reason IS NOT NULL AND action IS NOT NULL AND resolved_at IS NOT NULL)", name=op.f('ck_escalations_resolved_needs_reason')),
    sa.CheckConstraint("status IN ('open', 'resolved')", name=op.f('ck_escalations_status_valid')),
    sa.CheckConstraint("type IN ('not_submitted', 'missing', 'dropped', 'incorrect', 'aging', 'rejection_limit', 'panel_sla', 'past_start')", name=op.f('ck_escalations_type_valid')),
    sa.CheckConstraint('level IN (1, 2)', name=op.f('ck_escalations_level_valid')),
    sa.ForeignKeyConstraint(['demand_id'], ['demands.id'], name=op.f('fk_escalations_demand_id_demands')),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], name=op.f('fk_escalations_resolved_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_escalations'))
    )
    op.create_index(op.f('ix_escalations_demand_id'), 'escalations', ['demand_id'], unique=False)
    op.create_index('ix_escalations_status_due', 'escalations', ['status', 'due_at'], unique=False)
    op.create_index('uq_escalations_open_per_type', 'escalations', ['demand_id', 'type'], unique=True, postgresql_where=sa.text("status = 'open'"))
    op.create_table('gtd_submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('demand_id', sa.Integer(), nullable=False),
    sa.Column('gtd_req_id', sa.String(length=20), nullable=False),
    sa.Column('submitted_by', sa.Integer(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('previous_submission_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['demand_id'], ['demands.id'], name=op.f('fk_gtd_submissions_demand_id_demands')),
    sa.ForeignKeyConstraint(['previous_submission_id'], ['gtd_submissions.id'], name=op.f('fk_gtd_submissions_previous_submission_id_gtd_submissions')),
    sa.ForeignKeyConstraint(['submitted_by'], ['users.id'], name=op.f('fk_gtd_submissions_submitted_by_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gtd_submissions')),
    sa.UniqueConstraint('gtd_req_id', name=op.f('uq_gtd_submissions_gtd_req_id'))
    )
    op.create_index(op.f('ix_gtd_submissions_demand_id'), 'gtd_submissions', ['demand_id'], unique=False)
    op.create_table('stage_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('demand_id', sa.Integer(), nullable=False),
    sa.Column('from_stage', sa.String(length=24), nullable=True),
    sa.Column('to_stage', sa.String(length=24), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('origin', sa.String(length=10), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('import_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("origin IN ('app', 'import')", name=op.f('ck_stage_events_origin_valid')),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_stage_events_actor_id_users')),
    sa.ForeignKeyConstraint(['demand_id'], ['demands.id'], name=op.f('fk_stage_events_demand_id_demands')),
    sa.ForeignKeyConstraint(['import_id'], ['excel_imports.id'], name=op.f('fk_stage_events_import_id_excel_imports')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stage_events'))
    )
    op.create_index('ix_stage_events_demand_at', 'stage_events', ['demand_id', 'at'], unique=False)
    op.create_table('excel_rows',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('import_id', sa.Integer(), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=False),
    sa.Column('gtd_req_id', sa.String(length=20), nullable=True),
    sa.Column('demand_request_name', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=80), nullable=True),
    sa.Column('status_group', sa.String(length=80), nullable=True),
    sa.Column('source', sa.String(length=120), nullable=True),
    sa.Column('candidate_name', sa.String(length=160), nullable=True),
    sa.Column('doj', sa.Date(), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('submission_id', sa.Integer(), nullable=True),
    sa.Column('match_tier', sa.SmallInteger(), nullable=True),
    sa.CheckConstraint('match_tier IS NULL OR match_tier BETWEEN 1 AND 3', name=op.f('ck_excel_rows_tier_valid')),
    sa.ForeignKeyConstraint(['import_id'], ['excel_imports.id'], name=op.f('fk_excel_rows_import_id_excel_imports'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['submission_id'], ['gtd_submissions.id'], name=op.f('fk_excel_rows_submission_id_gtd_submissions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_excel_rows'))
    )
    op.create_index(op.f('ix_excel_rows_gtd_req_id'), 'excel_rows', ['gtd_req_id'], unique=False)
    op.create_index(op.f('ix_excel_rows_import_id'), 'excel_rows', ['import_id'], unique=False)
    op.create_table('interviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('demand_id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('round', sa.String(length=10), nullable=False),
    sa.Column('interviewer_id', sa.Integer(), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('feedback_token', sa.String(length=64), nullable=False),
    sa.Column('ratings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('outcome', sa.String(length=10), nullable=True),
    sa.Column('comments', sa.Text(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("outcome IS NULL OR outcome IN ('select', 'reject', 'hold')", name=op.f('ck_interviews_outcome_valid')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_interviews_candidate_id_candidates')),
    sa.ForeignKeyConstraint(['demand_id'], ['demands.id'], name=op.f('fk_interviews_demand_id_demands')),
    sa.ForeignKeyConstraint(['interviewer_id'], ['users.id'], name=op.f('fk_interviews_interviewer_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interviews')),
    sa.UniqueConstraint('feedback_token', name=op.f('uq_interviews_feedback_token'))
    )
    op.create_index(op.f('ix_interviews_demand_id'), 'interviews', ['demand_id'], unique=False)
    op.create_index(op.f('ix_interviews_interviewer_id'), 'interviews', ['interviewer_id'], unique=False)
    op.create_table('offer_approvals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('bill_rate', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('cost_rate', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('margin_pct', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('route', sa.String(length=12), nullable=False),
    sa.Column('approver_id', sa.Integer(), nullable=True),
    sa.Column('decision', sa.String(length=10), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("decision IS NULL OR decision IN ('approved', 'declined')", name=op.f('ck_offer_approvals_decision_valid')),
    sa.CheckConstraint("route IN ('admin', 'leadership')", name=op.f('ck_offer_approvals_route_valid')),
    sa.ForeignKeyConstraint(['approver_id'], ['users.id'], name=op.f('fk_offer_approvals_approver_id_users')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_offer_approvals_candidate_id_candidates')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_offer_approvals'))
    )
    op.create_index(op.f('ix_offer_approvals_candidate_id'), 'offer_approvals', ['candidate_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_offer_approvals_candidate_id'), table_name='offer_approvals')
    op.drop_table('offer_approvals')
    op.drop_index(op.f('ix_interviews_interviewer_id'), table_name='interviews')
    op.drop_index(op.f('ix_interviews_demand_id'), table_name='interviews')
    op.drop_table('interviews')
    op.drop_index(op.f('ix_excel_rows_import_id'), table_name='excel_rows')
    op.drop_index(op.f('ix_excel_rows_gtd_req_id'), table_name='excel_rows')
    op.drop_table('excel_rows')
    op.drop_index('ix_stage_events_demand_at', table_name='stage_events')
    op.drop_table('stage_events')
    op.drop_index(op.f('ix_gtd_submissions_demand_id'), table_name='gtd_submissions')
    op.drop_table('gtd_submissions')
    op.drop_index('uq_escalations_open_per_type', table_name='escalations', postgresql_where=sa.text("status = 'open'"))
    op.drop_index('ix_escalations_status_due', table_name='escalations')
    op.drop_index(op.f('ix_escalations_demand_id'), table_name='escalations')
    op.drop_table('escalations')
    op.drop_index(op.f('ix_candidates_demand_id'), table_name='candidates')
    op.drop_table('candidates')
    op.drop_table('user_business_units')
    op.drop_index('ix_demands_owner', table_name='demands')
    op.drop_index('ix_demands_account_status', table_name='demands')
    op.drop_table('demands')
    op.drop_table('user_practices')
    op.drop_table('user_accounts')
    op.drop_index('ix_rate_cards_lookup', table_name='rate_cards')
    op.drop_table('rate_cards')
    op.drop_table('notification_batches')
    op.drop_table('interviewer_profiles')
    op.drop_table('excel_imports')
    op.drop_table('business_units')
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_table('users')
    op.drop_table('accounts')
    op.execute(sa.schema.DropSequence(sa.Sequence("demand_ref_seq")))
