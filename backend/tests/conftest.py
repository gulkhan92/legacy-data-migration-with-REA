"""Test database fixtures.

Tests need a PostgreSQL server with pgvector (`docker compose up -d db`). Each session creates a
throwaway database, applies every migration to it, and drops it afterwards, so tests never touch
the development database.
"""

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db import models
from app.db.models.enums import JobStatus, SourceKind


def _alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session")
def test_db_name() -> Iterator[str]:
    settings = get_settings()
    name = f"legacylift_test_{uuid.uuid4().hex[:10]}"
    admin_url = make_url(settings.database_admin_url).set(database="postgres")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.exit(f"PostgreSQL is not reachable ({exc}). Run `docker compose up -d db`.", 2)
    yield name
    with engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    engine.dispose()


@pytest.fixture(scope="session")
def admin_url(test_db_name: str) -> str:
    return make_url(get_settings().database_admin_url).set(database=test_db_name).render_as_string(False)


@pytest.fixture(scope="session")
def app_url(test_db_name: str) -> str:
    settings = get_settings()
    return (
        make_url(settings.database_url)
        .set(database=test_db_name, username=settings.app_db_user, password=settings.app_db_password)
        .render_as_string(False)
    )


@pytest.fixture(scope="session")
def migrated(admin_url: str) -> str:
    command.upgrade(_alembic_config(admin_url), "head")
    return admin_url


@pytest.fixture(scope="session")
def admin_engine(migrated: str) -> Iterator[Engine]:
    engine = create_engine(migrated)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_engine(migrated: str, app_url: str) -> Iterator[Engine]:
    engine = create_engine(app_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_factory(app_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=app_engine, expire_on_commit=False)


class Tenant:
    """Identifiers of one seeded organization."""

    def __init__(self, org_id: uuid.UUID, name: str) -> None:
        self.org_id = org_id
        self.name = name
        self.user_id = uuid.uuid4()
        self.source_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.schema_id = uuid.uuid4()
        self.field_id = uuid.uuid4()
        self.customer_id = uuid.uuid4()
        self.invoice_id = uuid.uuid4()


def _seed_tenant(session: Session, name: str) -> Tenant:
    tenant = Tenant(uuid.uuid4(), name)
    session.add(models.Organization(id=tenant.org_id, name=name, slug=f"{name}-{tenant.org_id.hex[:6]}"))
    session.flush()
    session.add(
        models.User(
            id=tenant.user_id,
            email=f"{name}-{tenant.org_id.hex[:6]}@example.test",
            password_hash="x",
            full_name=name,
        )
    )
    session.flush()
    session.add(models.Membership(org_id=tenant.org_id, user_id=tenant.user_id, role="admin"))
    session.add(
        models.Source(id=tenant.source_id, org_id=tenant.org_id, name=f"{name} source", kind=SourceKind.SQLITE)
    )
    session.flush()
    session.add(
        models.Job(id=tenant.job_id, org_id=tenant.org_id, source_id=tenant.source_id, status=JobStatus.CREATED)
    )
    session.add(models.TargetSchema(id=tenant.schema_id, org_id=tenant.org_id, name=f"{name} schema"))
    session.flush()
    session.add(
        models.TargetField(
            id=tenant.field_id,
            org_id=tenant.org_id,
            schema_id=tenant.schema_id,
            table_name="customers",
            name="email",
            data_type="text",
            description="Customer email address",
        )
    )
    session.flush()
    session.add(models.SourceColumn(org_id=tenant.org_id, job_id=tenant.job_id, table_name="t", column_name="c"))
    session.add(
        models.MappingMemory(
            org_id=tenant.org_id, source_signature=f"sig-{name}", source_text="email", target_field_id=tenant.field_id
        )
    )
    session.add(models.LlmCache(org_id=tenant.org_id, request_hash=f"h-{name}", task="t", response={}))
    session.add(
        models.LlmCall(org_id=tenant.org_id, provider="p", model="m", task="t", status="ok")
    )
    session.add(models.Customer(id=tenant.customer_id, org_id=tenant.org_id, job_id=tenant.job_id, email="a@b.test"))
    session.add(
        models.QuarantinedRow(
            org_id=tenant.org_id, job_id=tenant.job_id, target_table="customers", reason_code="x", raw_row={}
        )
    )
    session.add(models.AuditLog(org_id=tenant.org_id, user_id=tenant.user_id, action="seed"))
    session.flush()
    return tenant


@pytest.fixture(scope="session")
def tenants(admin_engine: Engine) -> tuple[Tenant, Tenant]:
    """Two organizations with one row in many tenant tables, written as the owner."""
    with Session(admin_engine) as session:
        a = _seed_tenant(session, "alpha")
        b = _seed_tenant(session, "bravo")
        session.add(models.TargetSchema(id=uuid.uuid4(), org_id=None, name="builtin-shared"))
        session.commit()
    return a, b


# ---------------------------------------------------------------------------------------------
# API fixtures (Phase 2)
# ---------------------------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.core.rate_limit import limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models.enums import Role  # noqa: E402

PASSWORD = "Correct-Horse-9-Battery"
CSRF = {"X-Requested-With": "LegacyLift"}


class ApiUsers:
    def __init__(self, org_id: uuid.UUID, other_org_id: uuid.UUID) -> None:
        self.org_id = org_id
        self.other_org_id = other_org_id


def make_user(engine: Engine, email: str, role: Role, org_id: uuid.UUID) -> uuid.UUID:
    with Session(engine) as session:
        user = models.User(email=email, password_hash=hash_password(PASSWORD), full_name=email)
        session.add(user)
        session.flush()
        session.add(models.Membership(org_id=org_id, user_id=user.id, role=role))
        session.commit()
        return user.id


@pytest.fixture(scope="session")
def api_users(admin_engine: Engine) -> ApiUsers:
    with Session(admin_engine) as session:
        org = models.Organization(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")
        other = models.Organization(name="Globex", slug=f"globex-{uuid.uuid4().hex[:6]}")
        session.add_all([org, other])
        session.commit()
        ids = ApiUsers(org.id, other.id)
    for role in Role:
        make_user(admin_engine, f"{role.value}@acme.test", role, ids.org_id)
    make_user(admin_engine, "admin@globex.test", Role.ADMIN, ids.other_org_id)
    return ids


@pytest.fixture
def client(app_engine: Engine, api_users: ApiUsers, tmp_path, monkeypatch) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    import app.db.session as session_module
    from app.main import create_app

    monkeypatch.setattr(session_module, "get_engine", lambda: app_engine)
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    limiter.enabled = False
    limiter.reset()
    with TestClient(create_app(), base_url="https://testserver", headers=CSRF) as test_client:
        yield test_client


def login_as(client: TestClient, email: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})
