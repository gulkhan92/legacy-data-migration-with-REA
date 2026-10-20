import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from tests.conftest import _alembic_config

EXPECTED_PUBLIC_TABLES = {
    "organizations", "users", "memberships", "refresh_tokens", "audit_log", "leads",
    "sources", "jobs", "job_events", "discovery_evidence", "source_columns", "mappings",
    "transform_rules", "quarantined_rows", "reconciliation_reports",
    "target_schemas", "target_fields", "mapping_memory",
    "llm_calls", "llm_cache",
    "plans", "subscriptions", "usage_events", "invoices", "invoice_lines", "daily_metrics",
}  # fmt: skip
EXPECTED_WAREHOUSE_TABLES = {
    "customers", "products", "orders", "order_items",
    "patients", "encounters", "conditions", "medications", "observations",
}  # fmt: skip
GLOBAL_TABLES = {"users", "refresh_tokens", "plans", "daily_metrics", "leads"}


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """An empty database used to exercise the full upgrade and downgrade cycle."""
    settings = get_settings()
    name = f"legacylift_mig_{uuid.uuid4().hex[:10]}"
    base = make_url(settings.database_admin_url)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    yield base.set(database=name).render_as_string(False)
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _tables(url: str) -> set[tuple[str, str]]:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('public', 'warehouse') AND table_type = 'BASE TABLE'"
            )
        ).all()
    engine.dispose()
    return {(r[0], r[1]) for r in rows}


def test_upgrade_creates_every_table(fresh_db: str) -> None:
    command.upgrade(_alembic_config(fresh_db), "head")
    tables = _tables(fresh_db)
    assert {t for s, t in tables if s == "public"} == EXPECTED_PUBLIC_TABLES | {"alembic_version"}
    assert {t for s, t in tables if s == "warehouse"} == EXPECTED_WAREHOUSE_TABLES


def test_downgrade_removes_everything_and_upgrade_works_again(fresh_db: str) -> None:
    config = _alembic_config(fresh_db)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    assert {t for s, t in _tables(fresh_db) if s == "public"} <= {"alembic_version"}
    assert not {t for s, t in _tables(fresh_db) if s == "warehouse"}
    command.upgrade(config, "head")
    assert len(_tables(fresh_db)) == len(EXPECTED_PUBLIC_TABLES) + len(EXPECTED_WAREHOUSE_TABLES) + 1


def test_downgrade_one_step_removes_policies_only(fresh_db: str) -> None:
    config = _alembic_config(fresh_db)
    command.upgrade(config, "head")
    command.downgrade(config, "0001")
    engine = create_engine(fresh_db)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_policies")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM pg_tables WHERE rowsecurity")).scalar_one() == 0
    engine.dispose()
    assert len(_tables(fresh_db)) == len(EXPECTED_PUBLIC_TABLES) + len(EXPECTED_WAREHOUSE_TABLES) + 1


def test_pgvector_extension_and_hnsw_cosine_indexes(admin_engine) -> None:  # type: ignore[no-untyped-def]
    with admin_engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar() == 1
        indexes = dict(
            conn.execute(
                text("SELECT tablename, indexdef FROM pg_indexes WHERE indexname LIKE 'ix_%_embedding_hnsw'")
            ).all()
        )
    assert set(indexes) == {"source_columns", "target_fields", "mapping_memory"}
    for definition in indexes.values():
        assert "USING hnsw" in definition
        assert "vector_cosine_ops" in definition


def test_embedding_columns_are_384_dimensions(admin_engine) -> None:  # type: ignore[no-untyped-def]
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE a.attname = 'embedding' AND c.relkind = 'r'"
            )
        ).all()
    assert {r[0] for r in rows} == {"source_columns", "target_fields", "mapping_memory"}
    assert {r[1] for r in rows} == {"vector(384)"}


def test_every_tenant_table_has_forced_rls(admin_engine) -> None:  # type: ignore[no-untyped-def]
    with admin_engine.connect() as conn:
        tenant_tables = {
            (r[0], r[1])
            for r in conn.execute(
                text(
                    "SELECT table_schema, table_name FROM information_schema.columns "
                    "WHERE column_name = 'org_id' AND table_schema IN ('public', 'warehouse')"
                )
            )
        }
        flags = {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute(
                text(
                    "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r'"
                )
            )
        }
        policies = {(r[0], r[1]) for r in conn.execute(text("SELECT schemaname, tablename FROM pg_policies"))}
    tenant_tables = {t for t in tenant_tables if t[1] not in GLOBAL_TABLES}
    # organizations is the tenant root and is protected on its id column.
    protected = tenant_tables | {("public", "organizations")}
    assert len(tenant_tables) >= 27
    for key in protected:
        assert flags[key] == (True, True), f"RLS not enabled and forced on {key}"
        assert key in policies, f"No policy on {key}"
    # Intentionally global tables carry no policy.
    for table in GLOBAL_TABLES:
        assert ("public", table) not in policies


def test_application_role_cannot_bypass_rls(admin_engine) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    with admin_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = :r"),
            {"r": settings.app_db_user},
        ).one()
        owners = {
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname IN ('public', 'warehouse')")
            )
        }
    assert tuple(row) == (False, False, False, False)
    assert settings.app_db_user not in owners
