"""row level security, tenancy function and the restricted application role

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19 09:00:00

Design notes
- The migration connection (owner) owns the tables. The application connects as a separate
  role that is not a superuser, does not own the tables and has no BYPASSRLS. FORCE ROW LEVEL
  SECURITY is also set, so the policies hold even for the table owner. A superuser still
  bypasses RLS by definition, which is why the application never connects as one.
- app_current_org_id() reads the transaction local setting app.org_id. When it is unset the
  function returns NULL, no policy predicate is true, and every tenant query returns nothing.
- Tables without a policy are intentionally global: users, refresh_tokens (looked up before
  an organization is known), plans, daily_metrics (platform wide admin metrics) and leads
  (anonymous public form).
"""
from alembic import op
from sqlalchemy import text

from app.core.config import get_settings

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Rows always belong to exactly one organization.
STRICT_TENANT_TABLES = [
    "memberships",
    "sources",
    "jobs",
    "job_events",
    "discovery_evidence",
    "source_columns",
    "mappings",
    "transform_rules",
    "quarantined_rows",
    "reconciliation_reports",
    "mapping_memory",
    "llm_calls",
    "llm_cache",
    "subscriptions",
    "usage_events",
    "invoices",
    "invoice_lines",
    "warehouse.customers",
    "warehouse.products",
    "warehouse.orders",
    "warehouse.order_items",
    "warehouse.patients",
    "warehouse.encounters",
    "warehouse.conditions",
    "warehouse.medications",
    "warehouse.observations",
]

# org_id NULL means a shared built in row that every organization can read but not change.
SHARED_READ_TABLES = ["target_schemas", "target_fields"]

# Read own rows only. Insert is allowed with org_id NULL (events before an organization is known).
AUDIT_TABLE = "audit_log"

APP_SCHEMAS = ["public", "warehouse"]


def _policy_name(table: str, suffix: str) -> str:
    return f"{table.split('.')[-1]}_{suffix}"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_org_id() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.org_id', true), '')::uuid $$
        """
    )

    for table in STRICT_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {_policy_name(table, 'tenant_isolation')} ON {table}
            USING (org_id = app_current_org_id())
            WITH CHECK (org_id = app_current_org_id())
            """
        )

    # organizations: a tenant sees and updates only its own row. Creation is an owner task.
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY organizations_tenant_isolation ON organizations
        USING (id = app_current_org_id())
        WITH CHECK (id = app_current_org_id())
        """
    )

    for table in SHARED_READ_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_read ON {table} FOR SELECT
            USING (org_id IS NULL OR org_id = app_current_org_id())
            """
        )
        for command, clause in (
            ("INSERT", "WITH CHECK (org_id = app_current_org_id())"),
            ("UPDATE", "USING (org_id = app_current_org_id()) WITH CHECK (org_id = app_current_org_id())"),
            ("DELETE", "USING (org_id = app_current_org_id())"),
        ):
            op.execute(f"CREATE POLICY {table}_{command.lower()} ON {table} FOR {command} {clause}")

    op.execute(f"ALTER TABLE {AUDIT_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {AUDIT_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {AUDIT_TABLE}_read ON {AUDIT_TABLE} FOR SELECT USING (org_id = app_current_org_id())"
    )
    op.execute(
        f"""
        CREATE POLICY {AUDIT_TABLE}_insert ON {AUDIT_TABLE} FOR INSERT
        WITH CHECK (org_id IS NULL OR org_id = app_current_org_id())
        """
    )

    _create_app_role_and_grants()


def _create_app_role_and_grants() -> None:
    settings = get_settings()
    bind = op.get_bind()
    role = bind.execute(text("SELECT quote_ident(:r)"), {"r": settings.app_db_user}).scalar_one()
    role_literal = bind.execute(text("SELECT quote_literal(:r)"), {"r": settings.app_db_user}).scalar_one()
    password = bind.execute(text("SELECT quote_literal(:p)"), {"p": settings.app_db_password}).scalar_one()

    # Roles are cluster wide, so creation is idempotent and the password is always refreshed.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {role_literal}) THEN
                CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {password};
            ELSE
                ALTER ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD {password};
            END IF;
        END
        $$
        """
    )

    op.execute(f"GRANT CONNECT ON DATABASE {_current_database()} TO {role}")
    for schema in APP_SCHEMAS:
        op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}")
        op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}")
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
        )
        op.execute(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT ON SEQUENCES TO {role}"
        )

    # Append only audit trail and read only catalog tables.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_log FROM {role}")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON plans FROM {role}")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON organizations FROM {role}")
    op.execute(f"GRANT UPDATE ON organizations TO {role}")


def _current_database() -> str:
    bind = op.get_bind()
    return bind.execute(text("SELECT quote_ident(current_database())")).scalar_one()


def downgrade() -> None:
    settings = get_settings()
    bind = op.get_bind()
    role = bind.execute(text("SELECT quote_ident(:r)"), {"r": settings.app_db_user}).scalar_one()
    exists = bind.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": settings.app_db_user}
    ).scalar()
    if exists:
        # Removes this database's privileges only. The role itself is cluster wide and is kept.
        op.execute(f"DROP OWNED BY {role}")

    for table in SHARED_READ_TABLES:
        for suffix in ("read", "insert", "update", "delete"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for suffix in ("read", "insert"):
        op.execute(f"DROP POLICY IF EXISTS {AUDIT_TABLE}_{suffix} ON {AUDIT_TABLE}")
    op.execute(f"ALTER TABLE {AUDIT_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {AUDIT_TABLE} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS organizations_tenant_isolation ON organizations")
    op.execute("ALTER TABLE organizations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")

    for table in STRICT_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table, 'tenant_isolation')} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP FUNCTION IF EXISTS app_current_org_id()")
