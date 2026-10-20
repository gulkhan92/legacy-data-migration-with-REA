import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.security import (
    Principal,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models import Membership, RefreshToken, User
from app.db.session import make_session_factory, scoped_session
from app.services.audit import AuditAction, record_audit


class AuthenticationFailed(Exception):
    """Raised for every login or refresh failure. The message is never shown to the caller."""


@dataclass(frozen=True)
class AuthResult:
    principal: Principal
    access_token: str
    refresh_token: str


def _factory(factory: sessionmaker[Session] | None) -> sessionmaker[Session]:
    return factory or make_session_factory()


def _issue_tokens(session: Session, user_id: uuid.UUID, org_id: uuid.UUID, role) -> AuthResult:  # type: ignore[no-untyped-def]
    settings = get_settings()
    raw, hashed = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            org_id=org_id,
            token_hash=hashed,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        )
    )
    session.flush()
    principal = Principal(user_id=user_id, org_id=org_id, role=role)
    return AuthResult(principal, create_access_token(principal), raw)


def login(
    email: str,
    password: str,
    *,
    ip: str | None = None,
    org_id: uuid.UUID | None = None,
    factory: sessionmaker[Session] | None = None,
) -> AuthResult:
    settings = get_settings()
    factory = _factory(factory)
    now = datetime.now(UTC)
    email = email.strip()
    authenticated: uuid.UUID | None = None

    with scoped_session(factory=factory) as session:
        user = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
        locked = bool(user and user.locked_until and user.locked_until > now)
        ok = verify_password(password, user.password_hash if user else None)
        if user is None:
            record_audit(session, AuditAction.LOGIN_FAILED, ip=ip, detail={"reason": "unknown_account"})
        elif locked:
            record_audit(session, AuditAction.LOGIN_LOCKED, user_id=user.id, ip=ip)
        elif not ok or not user.is_active:
            user.failed_login_count += 1
            detail = {"reason": "bad_password" if not ok else "inactive"}
            if user.failed_login_count >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                user.failed_login_count = 0
                detail["locked"] = True  # type: ignore[assignment]
            record_audit(session, AuditAction.LOGIN_FAILED, user_id=user.id, ip=ip, detail=detail)
        else:
            user.failed_login_count = 0
            user.locked_until = None
            if password_needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)
            authenticated = user.id
    # The failure paths above commit their counters and audit rows before raising.
    if authenticated is None:
        raise AuthenticationFailed

    user_id = authenticated
    with scoped_session(user_id=user_id, factory=factory) as session:
        stmt = select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at)
        memberships = session.scalars(stmt).all()
        chosen = next((m for m in memberships if org_id is None or m.org_id == org_id), None)
    if chosen is None:
        with scoped_session(factory=factory) as session:
            record_audit(
                session, AuditAction.LOGIN_FAILED, user_id=user_id, ip=ip, detail={"reason": "no_membership"}
            )
        raise AuthenticationFailed

    with scoped_session(org_id=chosen.org_id, user_id=user_id, factory=factory) as session:
        result = _issue_tokens(session, user_id, chosen.org_id, chosen.role)
        record_audit(session, AuditAction.LOGIN, org_id=chosen.org_id, user_id=user_id, ip=ip)
    return result


def refresh(
    raw_token: str, *, ip: str | None = None, factory: sessionmaker[Session] | None = None
) -> AuthResult:
    """Rotate a refresh token. Presenting an already used token revokes every session of the user."""
    factory = _factory(factory)
    now = datetime.now(UTC)
    failure: str | None = None
    result: AuthResult | None = None

    with scoped_session(factory=factory) as session:
        row = session.scalar(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
            .with_for_update()
        )
        if row is None:
            failure = "unknown"
        elif row.revoked_at is not None:
            session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            record_audit(session, AuditAction.REFRESH_REUSE, user_id=row.user_id, ip=ip)
            failure = "reuse"
        elif row.expires_at <= now:
            failure = "expired"
        else:
            user = session.get(User, row.user_id)
            if user is None or not user.is_active or (user.locked_until and user.locked_until > now):
                row.revoked_at = now
                failure = "user_unavailable"
            else:
                user_id, org_id, old_id = row.user_id, row.org_id, row.id
    if failure is not None:
        raise AuthenticationFailed

    with scoped_session(user_id=user_id, factory=factory) as session:
        membership = session.scalar(
            select(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id)
        )
        role = membership.role if membership else None

    with scoped_session(org_id=org_id, user_id=user_id, factory=factory) as session:
        old = session.get(RefreshToken, old_id)
        if role is None:
            old.revoked_at = now  # type: ignore[union-attr]
        else:
            result = _issue_tokens(session, user_id, org_id, role)
            old.revoked_at = now  # type: ignore[union-attr]
            old.replaced_by_id = session.scalar(  # type: ignore[union-attr]
                select(RefreshToken.id).where(
                    RefreshToken.token_hash == hash_refresh_token(result.refresh_token)
                )
            )
    if result is None:
        raise AuthenticationFailed
    return result


def logout(
    raw_token: str | None,
    principal: Principal | None,
    *,
    ip: str | None = None,
    factory: sessionmaker[Session] | None = None,
) -> None:
    factory = _factory(factory)
    now = datetime.now(UTC)
    if raw_token:
        with scoped_session(factory=factory) as session:
            session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.token_hash == hash_refresh_token(raw_token),
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
    if principal is not None:
        with scoped_session(org_id=principal.org_id, user_id=principal.user_id, factory=factory) as session:
            record_audit(
                session, AuditAction.LOGOUT, org_id=principal.org_id, user_id=principal.user_id, ip=ip
            )
