"""Log review: nothing a person typed, nothing from a migrated file and no credential reaches a log or the console.

A whole session runs with every logger at DEBUG: a failed and a successful sign in, an upload of a file full of personal data, the
profile, map and rules stages, a dry run, the load, an error path, the audit log read. Everything written to the logging system and to
standard output and error is then searched for what must never be there: the password, the session tokens, the email addresses and
other values of the migrated rows, a provider key, the storage encryption key.
"""

import logging

import pytest

from pydantic import SecretStr

from app.core.config import get_settings
from tests.conftest import PASSWORD, login_as
from tests.test_mapping_api import seeded  # noqa: F401

PEOPLE = [("Ingrid", "Halvorsen", "ingrid.halvorsen@privacy-check.example", "+47 555 01 234", "ssn-111-22-3333"), ("Tomas", "Brandt", "tomas.brandt@privacy-check.example", "+49 555 01 987", "ssn-444-55-6666")]
PROVIDER_KEY = "gsk_log_hygiene_sentinel_0123456789"


def csv_with_personal_data() -> bytes:
    lines = ["cust_no,f_nm,l_nm,email_adr,ph_no,tax_id"]
    for i in range(40):
        first, last, email, phone, tax = PEOPLE[i % 2]
        lines.append(f"{i + 1},{first},{last},{email.replace('@', f'{i}@')},{phone},{tax}")
    return ("\n".join(lines) + "\n").encode()


@pytest.fixture
def everything_logged(caplog, capfd):  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    logging.getLogger().setLevel(logging.DEBUG)
    return caplog, capfd


def test_no_credential_and_no_personal_data_reaches_a_log(client, seeded, everything_logged, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    caplog, capfd = everything_logged
    settings = get_settings()
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(PROVIDER_KEY))

    wrong = login_as(client, "analyst@acme.test", "Not-The-Password-1")
    assert wrong.status_code == 401
    ok = login_as(client, "analyst@acme.test")
    assert ok.status_code == 200
    cookies = [c.value for c in client.cookies.jar if c.value]
    assert cookies

    upload = client.post("/api/v1/sources/upload", files={"file": ("people.csv", csv_with_personal_data())})
    assert upload.status_code == 201
    source = upload.json()["id"]
    # an unreadable upload and an unknown job take the error paths
    assert client.post("/api/v1/sources/upload", files={"file": ("broken.sqlite", b"not a database")}).status_code in (400, 422)
    assert client.get("/api/v1/jobs/00000000-0000-4000-8000-000000000000").status_code == 404

    job = client.post("/api/v1/jobs", json={"source_id": source, "target_schema": "commerce"}).json()["id"]
    for stage in ("profile", "map", "rules"):
        assert client.post(f"/api/v1/jobs/{job}/stages/{stage}", json={}).status_code == 202
    client.post(f"/api/v1/jobs/{job}/mappings/approve-decided")
    client.post(f"/api/v1/jobs/{job}/dry-run")
    client.get(f"/api/v1/jobs/{job}/quarantine")
    client.get(f"/api/v1/jobs/{job}/events")
    login_as(client, "admin@acme.test")
    client.get("/api/v1/admin/audit-log")

    logged = "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''} {r.stack_info or ''} {getattr(r, 'args', '')}" for r in caplog.records)
    out, err = capfd.readouterr()
    haystack = f"{logged}\n{out}\n{err}"

    forbidden = {
        "the password": PASSWORD,
        "the rejected password": "Not-The-Password-1",
        "a provider key": PROVIDER_KEY,
        "the storage key": settings.upload_encryption_key,
        "the token signing secret": settings.jwt_secret,
        **{f"the session token {i}": value for i, value in enumerate(cookies)},
        **{f"personal data {value}": value for person in PEOPLE for value in person[2:]},
        "a person's name": "Halvorsen",
    }
    for label, needle in forbidden.items():
        assert needle and needle not in haystack, f"{label} was logged"
    assert "Bearer " not in haystack and "eyJ" not in haystack  # no authorization header and no signed token


def test_an_unexpected_error_is_logged_without_the_request_body(client, seeded, everything_logged, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The handler of unexpected errors records the failure and the route, not what was sent."""
    caplog, _ = everything_logged
    assert login_as(client, "analyst@acme.test").status_code == 200
    secret = "sentinel-body-value-not-to-be-logged-7731"

    import app.api.routes.jobs as jobs_module

    def boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise RuntimeError("something broke")

    monkeypatch.setattr(jobs_module, "_enrich", boom)
    response = client.post("/api/v1/jobs", json={"source_id": "00000000-0000-4000-8000-000000000000", "target_schema": secret})
    assert response.status_code >= 400
    assert secret not in "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
