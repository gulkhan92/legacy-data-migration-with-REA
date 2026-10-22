"""llm router support: richer call log, cache accounting and organization settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23 09:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("error_code", sa.String(length=40), nullable=True))
    op.add_column("llm_calls", sa.Column("prompt_version", sa.String(length=60), nullable=True))
    op.create_index("ix_llm_calls_org_id_created_at", "llm_calls", ["org_id", "created_at"])
    op.add_column("llm_cache", sa.Column("prompt_version", sa.String(length=60), nullable=True))
    op.add_column("llm_cache", sa.Column("tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("llm_cache", sa.Column("hits", sa.Integer(), server_default="0", nullable=False))
    op.add_column("llm_cache", sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "org_settings",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("llm_provider_order", postgresql.JSONB(astext_type=sa.Text()), server_default='["groq", "gemini"]', nullable=False),
        sa.Column("llm_job_token_budget", sa.Integer(), server_default="6000", nullable=False),
        sa.Column("llm_org_daily_token_budget", sa.Integer(), server_default="100000", nullable=False),
        sa.Column("llm_max_calls_per_job", sa.Integer(), server_default="2", nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], name=op.f("fk_org_settings_org_id_organizations"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name=op.f("fk_org_settings_updated_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("org_id", name=op.f("pk_org_settings")),
    )
    # Tenant isolation like every other tenant table (see migration 0002).
    op.execute("ALTER TABLE org_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY org_settings_tenant_isolation ON org_settings "
        "USING (org_id = app_current_org_id()) WITH CHECK (org_id = app_current_org_id())"
    )
    # Privileges for the application role come from the default privileges set in migration 0002.


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS org_settings_tenant_isolation ON org_settings")
    op.drop_table("org_settings")
    op.drop_column("llm_cache", "last_hit_at")
    op.drop_column("llm_cache", "hits")
    op.drop_column("llm_cache", "tokens")
    op.drop_column("llm_cache", "prompt_version")
    op.drop_index("ix_llm_calls_org_id_created_at", table_name="llm_calls")
    op.drop_column("llm_calls", "prompt_version")
    op.drop_column("llm_calls", "error_code")
