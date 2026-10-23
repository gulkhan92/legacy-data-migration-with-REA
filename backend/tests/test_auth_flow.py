import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal, create_access_token
from app.db import models
from app.db.models.enums import Role
from tests.conftest import CSRF, PASSWORD, ApiUsers, login_as, make_user


def _cookie_header(response, name: str) -> str:  # type: ignore[no-untyped-def]
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}="))


def test_login_sets_hardened_cookies_and_returns_profile(client) -> None:  # type: ignore[no-untyped-def]
    response = login_as(client, "analyst@acme.test")
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "analyst" and body["org_name"] == "Acme"
    assert "password" not in response.text
    for name in ("access_token", "refresh_token"):
        header = _cookie_header(response, name).lower()
        assert "httponly" in header and "secure" in header and "samesite=lax" in header
    assert "path=/api/v1/auth" in _cookie_header(response, "refresh_token").lower()
    assert "max-age=900" in _cookie_header(response, "access_token").lower()
    assert f"max-age={7 * 86400}" in _cookie_header(response, "refresh_token").lower()


def test_me_requires_authentication(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/auth/me").status_code == 401
    login_as(client, "viewer@acme.test")
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "viewer@acme.test"


def test_unknown_user_and_wrong_password_are_indistinguishable(client) -> None:  # type: ignore[no-untyped-def]
    wrong = login_as(client, "viewer@acme.test", "Wrong-Password-123")
    unknown = login_as(client, "nobody@acme.test", PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["detail"] == "Invalid credentials" and wrong.json()["error"]["code"] == "not_authenticated"


def test_email_is_case_insensitive(client) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "VIEWER@Acme.Test").status_code == 200


def test_refresh_rotates_and_old_token_is_rejected(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    first = client.cookies.get("refresh_token")
    assert client.post("/api/v1/auth/refresh").status_code == 200
    second = client.cookies.get("refresh_token")
    assert first != second
    # Replaying the first token is treated as theft: the whole family is revoked.
    client.cookies.set("refresh_token", first, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401
    client.cookies.set("refresh_token", second, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_tokens_are_stored_hashed(client, admin_engine) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "viewer@acme.test")
    raw = client.cookies.get("refresh_token")
    with Session(admin_engine) as session:
        hashes = set(session.scalars(select(models.RefreshToken.token_hash)))
    assert raw not in hashes
    assert all(len(h) == 64 for h in hashes)


def test_logout_revokes_the_refresh_token(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "viewer@acme.test")
    refresh_cookie = client.cookies.get("refresh_token")
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set("refresh_token", refresh_cookie, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_refresh_fails_after_membership_is_removed(client, admin_engine, api_users: ApiUsers) -> None:  # type: ignore[no-untyped-def]
    email = f"temp-{uuid.uuid4().hex[:6]}@acme.test"
    user_id = make_user(admin_engine, email, Role.ANALYST, api_users.org_id)
    login_as(client, email)
    with Session(admin_engine) as session:
        session.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": user_id})
        session.commit()
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_expired_tampered_and_foreign_tokens_are_rejected(client, api_users: ApiUsers) -> None:  # type: ignore[no-untyped-def]
    principal = Principal(uuid.uuid4(), api_users.org_id, Role.ADMIN)
    expired = create_access_token(principal, now=datetime.now(UTC) - timedelta(hours=1))
    forged = jwt.encode(
        {"typ": "access", "sub": str(principal.user_id), "org": str(principal.org_id), "role": "admin",
         "iat": 1, "exp": 4102444800},
        "some-other-secret-that-is-long-enough-1234567890",
        algorithm="HS256",
    )  # fmt: skip
    unsigned = jwt.encode({"sub": "x"}, "", algorithm="none")
    good = create_access_token(principal)
    tampered = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
    for token in (expired, forged, unsigned, tampered):
        client.cookies.set("access_token", token)
        assert client.get("/api/v1/auth/me").status_code == 401, token


def test_account_locks_after_repeated_failures(client, admin_engine, api_users: ApiUsers) -> None:  # type: ignore[no-untyped-def]
    email = f"lock-{uuid.uuid4().hex[:6]}@acme.test"
    user_id = make_user(admin_engine, email, Role.VIEWER, api_users.org_id)
    for _ in range(get_settings().max_failed_logins):
        assert login_as(client, email, "Wrong-Password-123").status_code == 401
    # Even the correct password is refused while the account is locked.
    assert login_as(client, email).status_code == 401
    with Session(admin_engine) as session:
        locked_until = session.scalar(select(models.User.locked_until).where(models.User.id == user_id))
        assert locked_until is not None and locked_until > datetime.now(UTC)
        session.execute(
            update(models.User).where(models.User.id == user_id).values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()
    assert login_as(client, email).status_code == 200


def test_successful_login_resets_failure_counter(client, admin_engine, api_users: ApiUsers) -> None:  # type: ignore[no-untyped-def]
    email = f"reset-{uuid.uuid4().hex[:6]}@acme.test"
    make_user(admin_engine, email, Role.VIEWER, api_users.org_id)
    for _ in range(get_settings().max_failed_logins - 1):
        login_as(client, email, "Wrong-Password-123")
    assert login_as(client, email).status_code == 200
    for _ in range(get_settings().max_failed_logins - 1):
        assert login_as(client, email, "Wrong-Password-123").status_code == 401
    assert login_as(client, email).status_code == 200


def test_inactive_user_cannot_log_in(client, admin_engine, api_users: ApiUsers) -> None:  # type: ignore[no-untyped-def]
    email = f"off-{uuid.uuid4().hex[:6]}@acme.test"
    user_id = make_user(admin_engine, email, Role.VIEWER, api_users.org_id)
    with Session(admin_engine) as session:
        session.execute(update(models.User).where(models.User.id == user_id).values(is_active=False))
        session.commit()
    assert login_as(client, email).status_code == 401


def test_login_requires_a_membership_in_the_requested_org(client, api_users: ApiUsers) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@acme.test", "password": PASSWORD, "org_id": str(api_users.other_org_id)},
    )
    assert response.status_code == 401


def test_security_events_are_audited_and_scoped_to_the_organization(client, api_users) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "viewer@acme.test", "Wrong-Password-123")
    login_as(client, "admin@acme.test")
    client.post("/api/v1/auth/logout")
    login_as(client, "admin@acme.test")
    entries = client.get("/api/v1/admin/audit-log", params={"limit": 200}).json()
    actions = {e["action"] for e in entries}
    assert {"auth.login", "auth.logout"} <= actions
    # Another organization's admin never sees these entries.
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    other = client.get("/api/v1/admin/audit-log", params={"limit": 200}).json()
    assert not {e["id"] for e in entries} & {e["id"] for e in other}


def test_csrf_header_is_required_on_every_state_changing_request(client) -> None:  # type: ignore[no-untyped-def]
    plain = {"email": "viewer@acme.test", "password": PASSWORD}
    no_header = client.post("/api/v1/auth/login", json=plain, headers={"X-Requested-With": ""})
    assert no_header.status_code == 403
    wrong = client.post("/api/v1/auth/login", json=plain, headers={"X-Requested-With": "other"})
    assert wrong.status_code == 403
    assert client.post("/api/v1/auth/login", json=plain, headers=CSRF).status_code == 200
    # Safe methods do not need it.
    assert client.get("/api/v1/auth/me", headers={"X-Requested-With": ""}).status_code == 200
    for method, path in (("post", "/api/v1/auth/logout"), ("post", "/api/v1/auth/refresh"),
                         ("post", "/api/v1/sources/upload")):  # fmt: skip
        assert getattr(client, method)(path, headers={"X-Requested-With": ""}).status_code == 403


def test_login_is_rate_limited(client) -> None:  # type: ignore[no-untyped-def]
    from app.core.rate_limit import limiter

    limiter.enabled = True
    limiter.reset()
    try:
        codes = [login_as(client, "ghost@acme.test", "Wrong-Password-123").status_code for _ in range(7)]
    finally:
        limiter.enabled = False
        limiter.reset()
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429, 429]


def test_security_headers_and_cors_allowlist(client) -> None:  # type: ignore[no-untyped-def]
    response = client.get("/health")
    headers = response.headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert "max-age=" in headers["strict-transport-security"]
    assert "default-src 'none'" in headers["content-security-policy"]
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers
    preflight = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-requested-with",
        },
    )
    assert preflight.status_code == 400


@pytest.mark.parametrize("secret_field", ["jwt_secret", "upload_encryption_key"])
def test_production_refuses_development_secrets(secret_field: str) -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError):
        Settings(environment="production")
    with pytest.raises(ValueError):
        Settings(environment="production", **{secret_field: "x" * 40})
