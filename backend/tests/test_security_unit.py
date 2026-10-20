import uuid

import pytest

from app.core.security import (
    Principal,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    validate_password,
    verify_password,
)
from app.db.models.enums import Role


def test_passwords_are_hashed_with_argon2id() -> None:
    hashed = hash_password("Correct-Horse-9-Battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password("Correct-Horse-9-Battery", hashed)
    assert not verify_password("correct-horse-9-battery", hashed)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")
    assert hash_password("Correct-Horse-9-Battery") != hashed  # salted


@pytest.mark.parametrize(
    "password,fragment",
    [
        ("Short1a", "at least 12"),
        ("alllowercase1234", "uppercase"),
        ("ALLUPPERCASE1234", "lowercase"),
        ("NoDigitsHereAtAll", "digit"),
        ("x" * 129, "at most 128"),
    ],
)
def test_password_policy_rejects_weak_passwords(password: str, fragment: str) -> None:
    assert any(fragment in p for p in validate_password(password))


def test_password_policy_accepts_a_strong_password_and_rejects_the_email_name() -> None:
    assert validate_password("Correct-Horse-9-Battery") == []
    assert validate_password("Johnsmith-Passw0rd-x", email="johnsmith@example.com")


def test_access_token_round_trip() -> None:
    principal = Principal(uuid.uuid4(), uuid.uuid4(), Role.ANALYST)
    assert decode_access_token(create_access_token(principal)) == principal
    assert decode_access_token("garbage") is None


def test_refresh_token_hash_is_deterministic_and_not_the_token() -> None:
    raw, hashed = new_refresh_token()
    assert hashed == hash_refresh_token(raw) and raw != hashed and len(raw) >= 48
    assert new_refresh_token()[0] != raw
