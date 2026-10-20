"""Password hashing, password policy, access tokens and refresh token helpers."""

import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.db.models.enums import Role

ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# Verified against when the account does not exist, so response time does not reveal it.
_DUMMY_HASH = _hasher.hash("not-a-real-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password(password: str, *, email: str | None = None) -> list[str]:
    """Return the list of policy violations (empty when the password is acceptable)."""
    settings = get_settings()
    problems: list[str] = []
    if len(password) < settings.password_min_length:
        problems.append(f"must be at least {settings.password_min_length} characters")
    if len(password) > 128:
        problems.append("must be at most 128 characters")
    if not re.search(r"[a-z]", password):
        problems.append("must contain a lowercase letter")
    if not re.search(r"[A-Z]", password):
        problems.append("must contain an uppercase letter")
    if not re.search(r"\d", password):
        problems.append("must contain a digit")
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and local in password.lower():
            problems.append("must not contain the email name")
    return problems


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    org_id: uuid.UUID
    role: Role


def create_access_token(principal: Principal, *, now: datetime | None = None) -> str:
    settings = get_settings()
    issued = now or datetime.now(UTC)
    claims = {
        "typ": ACCESS_TOKEN_TYPE,
        "sub": str(principal.user_id),
        "org": str(principal.org_id),
        "role": principal.role.value,
        "jti": uuid.uuid4().hex,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(minutes=settings.access_token_minutes)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Principal | None:
    """Return the principal for a valid, unexpired access token, otherwise None."""
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub", "org", "role", "typ"]},
        )
        if claims["typ"] != ACCESS_TOKEN_TYPE:
            return None
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            org_id=uuid.UUID(claims["org"]),
            role=Role(claims["role"]),
        )
    except (jwt.PyJWTError, ValueError, KeyError):
        return None


def new_refresh_token() -> tuple[str, str]:
    """Return (token for the cookie, hash to store). The raw token is never stored."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
