"""authentication support: user scoped membership lookup and refresh token organization

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19 12:00:00

Before login completes no organization is known, yet the caller must be able to read their own
memberships to choose one. A second, permissive SELECT policy on memberships allows rows whose
user_id equals the transaction local setting app.user_id. It never exposes other users' rows.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_user_id() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE POLICY memberships_own_read ON memberships FOR SELECT
        USING (user_id = app_current_user_id())
        """
    )
    op.add_column(
        "refresh_tokens",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_refresh_tokens_org_id_organizations",
        "refresh_tokens",
        "organizations",
        ["org_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_refresh_tokens_org_id", "refresh_tokens", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_org_id", table_name="refresh_tokens")
    op.drop_constraint("fk_refresh_tokens_org_id_organizations", "refresh_tokens", type_="foreignkey")
    op.drop_column("refresh_tokens", "org_id")
    op.execute("DROP POLICY IF EXISTS memberships_own_read ON memberships")
    op.execute("DROP FUNCTION IF EXISTS app_current_user_id()")
