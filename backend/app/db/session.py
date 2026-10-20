"""Engine and scoped sessions.

Tenant isolation is enforced by PostgreSQL Row Level Security. A session carries its organization
(and optionally the acting user) in `session.info` and applies both to every transaction it starts
through the `after_begin` hook, as the transaction local settings `app.org_id` and `app.user_id`.
A transaction that starts without an organization sees no tenant rows, so a missed call fails
closed. The user setting only unlocks the caller's own membership rows (needed during login).
"""

import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

TENANT_INFO_KEY = "org_id"
USER_INFO_KEY = "user_id"


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


def apply_scope(
    connection: Connection, org_id: uuid.UUID | None, user_id: uuid.UUID | None = None
) -> None:
    """Set the transaction local organization and user. None clears the value."""
    connection.execute(
        text("SELECT set_config('app.org_id', :org_id, true), set_config('app.user_id', :user_id, true)"),
        {"org_id": str(org_id) if org_id else "", "user_id": str(user_id) if user_id else ""},
    )


def _install_scope_hook(session: Session) -> None:
    @event.listens_for(session, "after_begin")
    def _on_begin(sess: Session, transaction: object, connection: Connection) -> None:
        apply_scope(connection, sess.info.get(TENANT_INFO_KEY), sess.info.get(USER_INFO_KEY))


def tenant_of(session: Session) -> uuid.UUID | None:
    value = session.info.get(TENANT_INFO_KEY)
    return value if isinstance(value, uuid.UUID) else None


@contextmanager
def scoped_session(
    *,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Open a session bound to an organization and/or user. Commits on success."""
    session = (factory or make_session_factory())()
    session.info[TENANT_INFO_KEY] = org_id
    session.info[USER_INFO_KEY] = user_id
    _install_scope_hook(session)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def tenant_session(
    org_id: uuid.UUID, factory: sessionmaker[Session] | None = None
) -> AbstractContextManager[Session]:
    return scoped_session(org_id=org_id, factory=factory)
