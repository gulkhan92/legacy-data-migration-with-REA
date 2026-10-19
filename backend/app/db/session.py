"""Engine and tenant aware sessions.

Tenant isolation is enforced by PostgreSQL Row Level Security. A session carries its organization
in `session.info["org_id"]` and applies it to every transaction it starts through the
`after_begin` hook, as the transaction local setting `app.org_id`. A transaction that starts
without an organization sees no tenant rows, so a missed call fails closed.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

TENANT_INFO_KEY = "org_id"


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    return factory


def apply_tenant(connection: Connection, org_id: uuid.UUID | None) -> None:
    """Set the transaction local organization. Passing None clears it."""
    connection.execute(
        text("SELECT set_config('app.org_id', :org_id, true)"),
        {"org_id": str(org_id) if org_id else ""},
    )


def _install_tenant_hook(session: Session) -> None:
    from sqlalchemy import event

    @event.listens_for(session, "after_begin")
    def _on_begin(sess: Session, transaction: object, connection: Connection) -> None:
        apply_tenant(connection, sess.info.get(TENANT_INFO_KEY))


def tenant_of(session: Session) -> uuid.UUID | None:
    value = session.info.get(TENANT_INFO_KEY)
    return value if isinstance(value, uuid.UUID) else None


@contextmanager
def tenant_session(
    org_id: uuid.UUID,
    factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Open a session bound to one organization. Commits on success, rolls back on error."""
    session = (factory or make_session_factory())()
    session.info[TENANT_INFO_KEY] = org_id
    _install_tenant_hook(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
