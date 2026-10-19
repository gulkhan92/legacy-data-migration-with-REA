"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-19 08:43:59.455385
"""
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS warehouse")
    op.create_table('daily_metrics',
    sa.Column('metric_date', sa.Date(), nullable=False),
    sa.Column('active_orgs', sa.Integer(), server_default='0', nullable=False),
    sa.Column('new_orgs', sa.Integer(), server_default='0', nullable=False),
    sa.Column('mrr_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('new_mrr_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('expansion_mrr_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('contraction_mrr_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('churned_mrr_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('usage_revenue_cents', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('jobs', sa.Integer(), server_default='0', nullable=False),
    sa.Column('records', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('tokens_groq', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('tokens_gemini', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('failovers', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cache_hits', sa.Integer(), server_default='0', nullable=False),
    sa.Column('success_rate', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('avg_duration_ms', sa.BigInteger(), nullable=True),
    sa.Column('quality_score', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('is_simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_daily_metrics')),
    sa.UniqueConstraint('metric_date', name=op.f('uq_daily_metrics_metric_date'))
    )
    op.create_table('leads',
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('company', sa.String(length=200), nullable=True),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('kind', sa.String(length=32), server_default='contact', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads'))
    )
    op.create_table('organizations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('industry', sa.String(length=100), nullable=True),
    sa.Column('region', sa.String(length=100), nullable=True),
    sa.Column('is_simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('slug', name=op.f('uq_organizations_slug'))
    )
    op.create_table('plans',
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('monthly_fee_cents', sa.Integer(), nullable=False),
    sa.Column('included_records', sa.BigInteger(), nullable=False),
    sa.Column('overage_cents_per_1000', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plans')),
    sa.UniqueConstraint('code', name=op.f('uq_plans_code'))
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('failed_login_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    op.create_index('uq_users_email_lower', 'users', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('audit_log',
    sa.Column('org_id', sa.UUID(), nullable=True),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity_type', sa.String(length=100), nullable=True),
    sa.Column('entity_id', sa.String(length=100), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_audit_log_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_log_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
    op.create_index(op.f('ix_audit_log_org_id'), 'audit_log', ['org_id'], unique=False)
    op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)
    op.create_table('llm_cache',
    sa.Column('request_hash', sa.String(length=64), nullable=False),
    sa.Column('task', sa.String(length=50), nullable=False),
    sa.Column('response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_llm_cache_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_cache')),
    sa.UniqueConstraint('org_id', 'request_hash', name='uq_llm_cache_org_hash')
    )
    op.create_index(op.f('ix_llm_cache_org_id'), 'llm_cache', ['org_id'], unique=False)
    op.create_table('memberships',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.Enum('admin', 'analyst', 'viewer', name='membership_role', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("role IN ('admin', 'analyst', 'viewer')", name=op.f('ck_memberships_membership_role')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_memberships_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_memberships_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_memberships')),
    sa.UniqueConstraint('org_id', 'user_id', name='uq_memberships_org_user')
    )
    op.create_index(op.f('ix_memberships_org_id'), 'memberships', ['org_id'], unique=False)
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=128), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    op.create_index(op.f('ix_refresh_tokens_user_id'), 'refresh_tokens', ['user_id'], unique=False)
    op.create_table('sources',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.Enum('sqlite', 'dotnet_binary', 'csv', name='source_kind', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=True),
    sa.Column('storage_path', sa.Text(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('is_sample', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('encrypted', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("kind IN ('sqlite', 'dotnet_binary', 'csv')", name=op.f('ck_sources_source_kind')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_sources_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_sources_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sources'))
    )
    op.create_index(op.f('ix_sources_created_by'), 'sources', ['created_by'], unique=False)
    op.create_index(op.f('ix_sources_org_id'), 'sources', ['org_id'], unique=False)
    op.create_table('subscriptions',
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('active', 'cancelled', name='subscription_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("status IN ('active', 'cancelled')", name=op.f('ck_subscriptions_subscription_status')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_subscriptions_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], name=op.f('fk_subscriptions_plan_id_plans'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_subscriptions'))
    )
    op.create_index(op.f('ix_subscriptions_org_id'), 'subscriptions', ['org_id'], unique=False)
    op.create_index(op.f('ix_subscriptions_plan_id'), 'subscriptions', ['plan_id'], unique=False)
    op.create_table('target_schemas',
    sa.Column('org_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('version', sa.String(length=32), server_default='1', nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_target_schemas_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_target_schemas'))
    )
    op.create_index(op.f('ix_target_schemas_org_id'), 'target_schemas', ['org_id'], unique=False)
    op.create_index('uq_target_schemas_scope_name_version', 'target_schemas', [sa.literal_column("coalesce(org_id, '00000000-0000-0000-0000-000000000000'::uuid)"), 'name', 'version'], unique=True)
    op.create_table('invoices',
    sa.Column('subscription_id', sa.UUID(), nullable=True),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('status', sa.Enum('paid', 'open', 'overdue', 'void', name='invoice_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('total_cents', sa.BigInteger(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("status IN ('paid', 'open', 'overdue', 'void')", name=op.f('ck_invoices_invoice_status')),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_invoices_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], name=op.f('fk_invoices_subscription_id_subscriptions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invoices'))
    )
    op.create_index(op.f('ix_invoices_org_id'), 'invoices', ['org_id'], unique=False)
    op.create_index(op.f('ix_invoices_subscription_id'), 'invoices', ['subscription_id'], unique=False)
    op.create_table('jobs',
    sa.Column('source_id', sa.UUID(), nullable=False),
    sa.Column('target_schema_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.Enum('CREATED', 'UPLOADED', 'DISCOVERING', 'PROFILED', 'MAPPED', 'AWAITING_REVIEW', 'DRY_RUN', 'READY', 'LOADING', 'RECONCILING', 'COMPLETED', 'FAILED', 'ROLLED_BACK', name='job_status', native_enum=False, create_constraint=False, length=32), server_default='CREATED', nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('token_budget', sa.Integer(), server_default='6000', nullable=False),
    sa.Column('tokens_used', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("status IN ('CREATED', 'UPLOADED', 'DISCOVERING', 'PROFILED', 'MAPPED', 'AWAITING_REVIEW', 'DRY_RUN', 'READY', 'LOADING', 'RECONCILING', 'COMPLETED', 'FAILED', 'ROLLED_BACK')", name=op.f('ck_jobs_job_status')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_jobs_created_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_jobs_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], name=op.f('fk_jobs_source_id_sources'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['target_schema_id'], ['target_schemas.id'], name=op.f('fk_jobs_target_schema_id_target_schemas'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index(op.f('ix_jobs_created_by'), 'jobs', ['created_by'], unique=False)
    op.create_index(op.f('ix_jobs_org_id'), 'jobs', ['org_id'], unique=False)
    op.create_index(op.f('ix_jobs_source_id'), 'jobs', ['source_id'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_index(op.f('ix_jobs_target_schema_id'), 'jobs', ['target_schema_id'], unique=False)
    op.create_table('target_fields',
    sa.Column('org_id', sa.UUID(), nullable=True),
    sa.Column('schema_id', sa.UUID(), nullable=False),
    sa.Column('table_name', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('data_type', sa.String(length=50), nullable=False),
    sa.Column('required', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('synonyms', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('examples', postgresql.ARRAY(sa.Text()), server_default='{}', nullable=False),
    sa.Column('pii', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_target_fields_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['schema_id'], ['target_schemas.id'], name=op.f('fk_target_fields_schema_id_target_schemas'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_target_fields')),
    sa.UniqueConstraint('schema_id', 'table_name', 'name', name='uq_target_fields_schema_table_name')
    )
    op.create_index('ix_target_fields_embedding_hnsw', 'target_fields', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('ix_target_fields_org_id'), 'target_fields', ['org_id'], unique=False)
    op.create_index(op.f('ix_target_fields_schema_id'), 'target_fields', ['schema_id'], unique=False)
    op.create_table('discovery_evidence',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('command', sa.String(length=255), nullable=False),
    sa.Column('rea_version', sa.String(length=50), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('raw_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_discovery_evidence_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_discovery_evidence_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_discovery_evidence'))
    )
    op.create_index(op.f('ix_discovery_evidence_job_id'), 'discovery_evidence', ['job_id'], unique=False)
    op.create_index(op.f('ix_discovery_evidence_org_id'), 'discovery_evidence', ['org_id'], unique=False)
    op.create_table('invoice_lines',
    sa.Column('invoice_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('base', 'overage', name='invoice_line_kind', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=18, scale=3), nullable=False),
    sa.Column('unit_price_cents', sa.Numeric(precision=14, scale=4), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("kind IN ('base', 'overage')", name=op.f('ck_invoice_lines_invoice_line_kind')),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name=op.f('fk_invoice_lines_invoice_id_invoices'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_invoice_lines_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invoice_lines'))
    )
    op.create_index(op.f('ix_invoice_lines_invoice_id'), 'invoice_lines', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_invoice_lines_org_id'), 'invoice_lines', ['org_id'], unique=False)
    op.create_table('job_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('from_status', sa.Enum('CREATED', 'UPLOADED', 'DISCOVERING', 'PROFILED', 'MAPPED', 'AWAITING_REVIEW', 'DRY_RUN', 'READY', 'LOADING', 'RECONCILING', 'COMPLETED', 'FAILED', 'ROLLED_BACK', name='job_event_from_status', native_enum=False, create_constraint=False, length=32), nullable=True),
    sa.Column('to_status', sa.Enum('CREATED', 'UPLOADED', 'DISCOVERING', 'PROFILED', 'MAPPED', 'AWAITING_REVIEW', 'DRY_RUN', 'READY', 'LOADING', 'RECONCILING', 'COMPLETED', 'FAILED', 'ROLLED_BACK', name='job_event_to_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("from_status IN ('CREATED', 'UPLOADED', 'DISCOVERING', 'PROFILED', 'MAPPED', 'AWAITING_REVIEW', 'DRY_RUN', 'READY', 'LOADING', 'RECONCILING', 'COMPLETED', 'FAILED', 'ROLLED_BACK')", name=op.f('ck_job_events_job_event_from_status')),
    sa.CheckConstraint("to_status IN ('CREATED', 'UPLOADED', 'DISCOVERING', 'PROFILED', 'MAPPED', 'AWAITING_REVIEW', 'DRY_RUN', 'READY', 'LOADING', 'RECONCILING', 'COMPLETED', 'FAILED', 'ROLLED_BACK')", name=op.f('ck_job_events_job_event_to_status')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_events_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_job_events_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_events'))
    )
    op.create_index(op.f('ix_job_events_job_id'), 'job_events', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_events_org_id'), 'job_events', ['org_id'], unique=False)
    op.create_table('llm_calls',
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('task', sa.String(length=50), nullable=False),
    sa.Column('tokens_in', sa.Integer(), server_default='0', nullable=False),
    sa.Column('tokens_out', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cached_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('latency_ms', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.Enum('ok', 'cache_hit', 'rate_limited', 'timeout', 'schema_error', 'error', name='llm_call_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('cache_hit', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("status IN ('ok', 'cache_hit', 'rate_limited', 'timeout', 'schema_error', 'error')", name=op.f('ck_llm_calls_llm_call_status')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_llm_calls_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_llm_calls_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_llm_calls'))
    )
    op.create_index(op.f('ix_llm_calls_job_id'), 'llm_calls', ['job_id'], unique=False)
    op.create_index(op.f('ix_llm_calls_org_id'), 'llm_calls', ['org_id'], unique=False)
    op.create_table('mapping_memory',
    sa.Column('source_signature', sa.String(length=64), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.Column('target_field_id', sa.UUID(), nullable=False),
    sa.Column('transform_hint', sa.Text(), nullable=True),
    sa.Column('times_approved', sa.Integer(), server_default='1', nullable=False),
    sa.Column('last_approved_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_mapping_memory_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_field_id'], ['target_fields.id'], name=op.f('fk_mapping_memory_target_field_id_target_fields'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mapping_memory')),
    sa.UniqueConstraint('org_id', 'source_signature', 'target_field_id', name='uq_mapping_memory_signature')
    )
    op.create_index('ix_mapping_memory_embedding_hnsw', 'mapping_memory', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('ix_mapping_memory_org_id'), 'mapping_memory', ['org_id'], unique=False)
    op.create_index(op.f('ix_mapping_memory_target_field_id'), 'mapping_memory', ['target_field_id'], unique=False)
    op.create_table('quarantined_rows',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('target_table', sa.String(length=100), nullable=False),
    sa.Column('source_row_number', sa.BigInteger(), nullable=True),
    sa.Column('reason_code', sa.String(length=64), nullable=False),
    sa.Column('reason_detail', sa.Text(), nullable=True),
    sa.Column('raw_row', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_quarantined_rows_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_quarantined_rows_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quarantined_rows'))
    )
    op.create_index(op.f('ix_quarantined_rows_job_id'), 'quarantined_rows', ['job_id'], unique=False)
    op.create_index(op.f('ix_quarantined_rows_org_id'), 'quarantined_rows', ['org_id'], unique=False)
    op.create_index(op.f('ix_quarantined_rows_reason_code'), 'quarantined_rows', ['reason_code'], unique=False)
    op.create_table('reconciliation_reports',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('source_rows', sa.BigInteger(), nullable=False),
    sa.Column('loaded_rows', sa.BigInteger(), nullable=False),
    sa.Column('quarantined_rows', sa.BigInteger(), nullable=False),
    sa.Column('quality_before', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('quality_after', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('checks', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_reconciliation_reports_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_reconciliation_reports_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reconciliation_reports')),
    sa.UniqueConstraint('job_id', name=op.f('uq_reconciliation_reports_job_id'))
    )
    op.create_index(op.f('ix_reconciliation_reports_org_id'), 'reconciliation_reports', ['org_id'], unique=False)
    op.create_table('source_columns',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('table_name', sa.String(length=255), nullable=False),
    sa.Column('column_name', sa.String(length=255), nullable=False),
    sa.Column('normalized_name', sa.String(length=255), nullable=True),
    sa.Column('inferred_type', sa.String(length=50), nullable=True),
    sa.Column('semantic_type', sa.String(length=50), nullable=True),
    sa.Column('pattern_class', sa.String(length=100), nullable=True),
    sa.Column('null_rate', sa.Numeric(precision=6, scale=5), nullable=True),
    sa.Column('distinct_count', sa.BigInteger(), nullable=True),
    sa.Column('pii', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('discovery_confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('profile', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_source_columns_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_source_columns_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_columns'))
    )
    op.create_index('ix_source_columns_embedding_hnsw', 'source_columns', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('ix_source_columns_job_id'), 'source_columns', ['job_id'], unique=False)
    op.create_index(op.f('ix_source_columns_org_id'), 'source_columns', ['org_id'], unique=False)
    op.create_index('uq_source_columns_job_table_column', 'source_columns', ['job_id', 'table_name', 'column_name'], unique=True)
    op.create_table('usage_events',
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('records_loaded', sa.BigInteger(), nullable=False),
    sa.Column('tokens_used', sa.Integer(), server_default='0', nullable=False),
    sa.Column('duration_ms', sa.BigInteger(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_simulated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_usage_events_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_usage_events_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_usage_events'))
    )
    op.create_index(op.f('ix_usage_events_job_id'), 'usage_events', ['job_id'], unique=False)
    op.create_index(op.f('ix_usage_events_occurred_at'), 'usage_events', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_usage_events_org_id'), 'usage_events', ['org_id'], unique=False)
    op.create_table('customers',
    sa.Column('first_name', sa.String(length=100), nullable=True),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('company', sa.String(length=200), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('address_line1', sa.String(length=200), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('state', sa.String(length=100), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('country', sa.String(length=100), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_customers_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_customers_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_customers_job_id'), 'customers', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_customers_org_id'), 'customers', ['org_id'], unique=False, schema='warehouse')
    op.create_table('patients',
    sa.Column('first_name', sa.String(length=100), nullable=True),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('death_date', sa.Date(), nullable=True),
    sa.Column('gender', sa.String(length=16), nullable=True),
    sa.Column('ssn', sa.String(length=20), nullable=True),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('state', sa.String(length=100), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('is_deceased', sa.Boolean(), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_patients_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_patients_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_patients')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_patients_job_id'), 'patients', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_patients_org_id'), 'patients', ['org_id'], unique=False, schema='warehouse')
    op.create_table('products',
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_products_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_products_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_products')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_products_job_id'), 'products', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_products_org_id'), 'products', ['org_id'], unique=False, schema='warehouse')
    op.create_table('mappings',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('source_column_id', sa.UUID(), nullable=False),
    sa.Column('target_field_id', sa.UUID(), nullable=True),
    sa.Column('method', sa.Enum('memory', 'embedding', 'llm', 'human', name='mapping_method', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('suggested', 'approved', 'overridden', 'rejected', name='mapping_status', native_enum=False, create_constraint=False, length=32), server_default='suggested', nullable=False),
    sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('similarity', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('transform_hint', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.CheckConstraint("method IN ('memory', 'embedding', 'llm', 'human')", name=op.f('ck_mappings_mapping_method')),
    sa.CheckConstraint("status IN ('suggested', 'approved', 'overridden', 'rejected')", name=op.f('ck_mappings_mapping_status')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_mappings_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_mappings_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_mappings_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['source_column_id'], ['source_columns.id'], name=op.f('fk_mappings_source_column_id_source_columns'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_field_id'], ['target_fields.id'], name=op.f('fk_mappings_target_field_id_target_fields'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_mappings'))
    )
    op.create_index(op.f('ix_mappings_job_id'), 'mappings', ['job_id'], unique=False)
    op.create_index(op.f('ix_mappings_org_id'), 'mappings', ['org_id'], unique=False)
    op.create_index(op.f('ix_mappings_reviewed_by'), 'mappings', ['reviewed_by'], unique=False)
    op.create_index(op.f('ix_mappings_source_column_id'), 'mappings', ['source_column_id'], unique=False)
    op.create_index(op.f('ix_mappings_target_field_id'), 'mappings', ['target_field_id'], unique=False)
    op.create_index('uq_mappings_job_source_column', 'mappings', ['job_id', 'source_column_id'], unique=True)
    op.create_table('encounters',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('encounter_class', sa.String(length=50), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('total_cost_cents', sa.BigInteger(), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_encounters_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_encounters_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['warehouse.patients.id'], name=op.f('fk_encounters_patient_id_patients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_encounters')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_encounters_job_id'), 'encounters', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_encounters_org_id'), 'encounters', ['org_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_encounters_patient_id'), 'encounters', ['patient_id'], unique=False, schema='warehouse')
    op.create_table('orders',
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('order_date', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('billing_city', sa.String(length=100), nullable=True),
    sa.Column('billing_country', sa.String(length=100), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['warehouse.customers.id'], name=op.f('fk_orders_customer_id_customers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_orders_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_orders_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_orders_customer_id'), 'orders', ['customer_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_orders_job_id'), 'orders', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_orders_org_id'), 'orders', ['org_id'], unique=False, schema='warehouse')
    op.create_table('transform_rules',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('mapping_id', sa.UUID(), nullable=False),
    sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('sample_failure_count', sa.Integer(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_transform_rules_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['mapping_id'], ['mappings.id'], name=op.f('fk_transform_rules_mapping_id_mappings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_transform_rules_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transform_rules'))
    )
    op.create_index(op.f('ix_transform_rules_job_id'), 'transform_rules', ['job_id'], unique=False)
    op.create_index(op.f('ix_transform_rules_mapping_id'), 'transform_rules', ['mapping_id'], unique=False)
    op.create_index(op.f('ix_transform_rules_org_id'), 'transform_rules', ['org_id'], unique=False)
    op.create_table('conditions',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('encounter_id', sa.UUID(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('stop_date', sa.Date(), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['encounter_id'], ['warehouse.encounters.id'], name=op.f('fk_conditions_encounter_id_encounters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_conditions_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_conditions_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['warehouse.patients.id'], name=op.f('fk_conditions_patient_id_patients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conditions')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_conditions_encounter_id'), 'conditions', ['encounter_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_conditions_job_id'), 'conditions', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_conditions_org_id'), 'conditions', ['org_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_conditions_patient_id'), 'conditions', ['patient_id'], unique=False, schema='warehouse')
    op.create_table('medications',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('encounter_id', sa.UUID(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('stop_date', sa.Date(), nullable=True),
    sa.Column('total_cost_cents', sa.BigInteger(), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['encounter_id'], ['warehouse.encounters.id'], name=op.f('fk_medications_encounter_id_encounters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_medications_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_medications_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['warehouse.patients.id'], name=op.f('fk_medications_patient_id_patients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_medications')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_medications_encounter_id'), 'medications', ['encounter_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_medications_job_id'), 'medications', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_medications_org_id'), 'medications', ['org_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_medications_patient_id'), 'medications', ['patient_id'], unique=False, schema='warehouse')
    op.create_table('observations',
    sa.Column('patient_id', sa.UUID(), nullable=False),
    sa.Column('encounter_id', sa.UUID(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('value', sa.String(length=100), nullable=True),
    sa.Column('units', sa.String(length=50), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['encounter_id'], ['warehouse.encounters.id'], name=op.f('fk_observations_encounter_id_encounters'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_observations_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_observations_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['patient_id'], ['warehouse.patients.id'], name=op.f('fk_observations_patient_id_patients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_observations')),
    schema='warehouse'
    )
    op.create_index('ix_observations_patient_id_code', 'observations', ['patient_id', 'code'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_observations_encounter_id'), 'observations', ['encounter_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_observations_job_id'), 'observations', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_observations_org_id'), 'observations', ['org_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_observations_patient_id'), 'observations', ['patient_id'], unique=False, schema='warehouse')
    op.create_table('order_items',
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('product_id', sa.UUID(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('source_key', sa.String(length=100), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('org_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_order_items_job_id_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['order_id'], ['warehouse.orders.id'], name=op.f('fk_order_items_order_id_orders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_order_items_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['warehouse.products.id'], name=op.f('fk_order_items_product_id_products'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_items')),
    schema='warehouse'
    )
    op.create_index(op.f('ix_warehouse_order_items_job_id'), 'order_items', ['job_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_order_items_order_id'), 'order_items', ['order_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_order_items_org_id'), 'order_items', ['org_id'], unique=False, schema='warehouse')
    op.create_index(op.f('ix_warehouse_order_items_product_id'), 'order_items', ['product_id'], unique=False, schema='warehouse')


def downgrade() -> None:
    op.drop_index(op.f('ix_warehouse_order_items_product_id'), table_name='order_items', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_order_items_org_id'), table_name='order_items', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_order_items_order_id'), table_name='order_items', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_order_items_job_id'), table_name='order_items', schema='warehouse')
    op.drop_table('order_items', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_observations_patient_id'), table_name='observations', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_observations_org_id'), table_name='observations', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_observations_job_id'), table_name='observations', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_observations_encounter_id'), table_name='observations', schema='warehouse')
    op.drop_index('ix_observations_patient_id_code', table_name='observations', schema='warehouse')
    op.drop_table('observations', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_medications_patient_id'), table_name='medications', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_medications_org_id'), table_name='medications', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_medications_job_id'), table_name='medications', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_medications_encounter_id'), table_name='medications', schema='warehouse')
    op.drop_table('medications', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_conditions_patient_id'), table_name='conditions', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_conditions_org_id'), table_name='conditions', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_conditions_job_id'), table_name='conditions', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_conditions_encounter_id'), table_name='conditions', schema='warehouse')
    op.drop_table('conditions', schema='warehouse')
    op.drop_index(op.f('ix_transform_rules_org_id'), table_name='transform_rules')
    op.drop_index(op.f('ix_transform_rules_mapping_id'), table_name='transform_rules')
    op.drop_index(op.f('ix_transform_rules_job_id'), table_name='transform_rules')
    op.drop_table('transform_rules')
    op.drop_index(op.f('ix_warehouse_orders_org_id'), table_name='orders', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_orders_job_id'), table_name='orders', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_orders_customer_id'), table_name='orders', schema='warehouse')
    op.drop_table('orders', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_encounters_patient_id'), table_name='encounters', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_encounters_org_id'), table_name='encounters', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_encounters_job_id'), table_name='encounters', schema='warehouse')
    op.drop_table('encounters', schema='warehouse')
    op.drop_index('uq_mappings_job_source_column', table_name='mappings')
    op.drop_index(op.f('ix_mappings_target_field_id'), table_name='mappings')
    op.drop_index(op.f('ix_mappings_source_column_id'), table_name='mappings')
    op.drop_index(op.f('ix_mappings_reviewed_by'), table_name='mappings')
    op.drop_index(op.f('ix_mappings_org_id'), table_name='mappings')
    op.drop_index(op.f('ix_mappings_job_id'), table_name='mappings')
    op.drop_table('mappings')
    op.drop_index(op.f('ix_warehouse_products_org_id'), table_name='products', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_products_job_id'), table_name='products', schema='warehouse')
    op.drop_table('products', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_patients_org_id'), table_name='patients', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_patients_job_id'), table_name='patients', schema='warehouse')
    op.drop_table('patients', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_customers_org_id'), table_name='customers', schema='warehouse')
    op.drop_index(op.f('ix_warehouse_customers_job_id'), table_name='customers', schema='warehouse')
    op.drop_table('customers', schema='warehouse')
    op.drop_index(op.f('ix_usage_events_org_id'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_occurred_at'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_job_id'), table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_index('uq_source_columns_job_table_column', table_name='source_columns')
    op.drop_index(op.f('ix_source_columns_org_id'), table_name='source_columns')
    op.drop_index(op.f('ix_source_columns_job_id'), table_name='source_columns')
    op.drop_index('ix_source_columns_embedding_hnsw', table_name='source_columns', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('source_columns')
    op.drop_index(op.f('ix_reconciliation_reports_org_id'), table_name='reconciliation_reports')
    op.drop_table('reconciliation_reports')
    op.drop_index(op.f('ix_quarantined_rows_reason_code'), table_name='quarantined_rows')
    op.drop_index(op.f('ix_quarantined_rows_org_id'), table_name='quarantined_rows')
    op.drop_index(op.f('ix_quarantined_rows_job_id'), table_name='quarantined_rows')
    op.drop_table('quarantined_rows')
    op.drop_index(op.f('ix_mapping_memory_target_field_id'), table_name='mapping_memory')
    op.drop_index(op.f('ix_mapping_memory_org_id'), table_name='mapping_memory')
    op.drop_index('ix_mapping_memory_embedding_hnsw', table_name='mapping_memory', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('mapping_memory')
    op.drop_index(op.f('ix_llm_calls_org_id'), table_name='llm_calls')
    op.drop_index(op.f('ix_llm_calls_job_id'), table_name='llm_calls')
    op.drop_table('llm_calls')
    op.drop_index(op.f('ix_job_events_org_id'), table_name='job_events')
    op.drop_index(op.f('ix_job_events_job_id'), table_name='job_events')
    op.drop_table('job_events')
    op.drop_index(op.f('ix_invoice_lines_org_id'), table_name='invoice_lines')
    op.drop_index(op.f('ix_invoice_lines_invoice_id'), table_name='invoice_lines')
    op.drop_table('invoice_lines')
    op.drop_index(op.f('ix_discovery_evidence_org_id'), table_name='discovery_evidence')
    op.drop_index(op.f('ix_discovery_evidence_job_id'), table_name='discovery_evidence')
    op.drop_table('discovery_evidence')
    op.drop_index(op.f('ix_target_fields_schema_id'), table_name='target_fields')
    op.drop_index(op.f('ix_target_fields_org_id'), table_name='target_fields')
    op.drop_index('ix_target_fields_embedding_hnsw', table_name='target_fields', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('target_fields')
    op.drop_index(op.f('ix_jobs_target_schema_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_source_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_org_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_created_by'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_invoices_subscription_id'), table_name='invoices')
    op.drop_index(op.f('ix_invoices_org_id'), table_name='invoices')
    op.drop_table('invoices')
    op.drop_index('uq_target_schemas_scope_name_version', table_name='target_schemas')
    op.drop_index(op.f('ix_target_schemas_org_id'), table_name='target_schemas')
    op.drop_table('target_schemas')
    op.drop_index(op.f('ix_subscriptions_plan_id'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_org_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_sources_org_id'), table_name='sources')
    op.drop_index(op.f('ix_sources_created_by'), table_name='sources')
    op.drop_table('sources')
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_index(op.f('ix_memberships_org_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_index(op.f('ix_llm_cache_org_id'), table_name='llm_cache')
    op.drop_table('llm_cache')
    op.drop_index(op.f('ix_audit_log_user_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_org_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index('uq_users_email_lower', table_name='users')
    op.drop_table('users')
    op.drop_table('plans')
    op.drop_table('organizations')
    op.drop_table('leads')
    op.drop_table('daily_metrics')
    op.execute("DROP SCHEMA IF EXISTS warehouse CASCADE")
