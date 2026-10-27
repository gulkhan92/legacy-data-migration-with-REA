"""Hostile input against the endpoints that take free text, ids and files. These are the probes of the Phase 13 security pass: none may
reach the database as code, none may answer with an internal error, and none may show a person's data to another organization."""

import pytest

from tests.conftest import login_as
from tests.test_mapping_api import seeded  # noqa: F401

HOSTILE = [
    "' OR '1'='1",
    "'; DROP TABLE jobs; --",
    "\" OR \"\"=\"",
    "%",
    "_",
    "\\",
    "%' AND 1=(SELECT count(*) FROM users) --",
    "<script>alert(1)</script>",
    "a" * 99,
    "\u202e\u0000",  # a right to left override and a null byte, which PostgreSQL text cannot hold
    "😀́",  # an emoji and a combining accent
    "../../etc/passwd",
]

SEARCHES = [
    "/api/v1/jobs?q={}",
    "/api/v1/mapping-memory?q={}",
    "/api/v1/mapping-memory?schema={}",
    "/api/v1/admin/users?q={}",
    "/api/v1/admin/audit-log?q={}",
    "/api/v1/analytics/invoices?q={}",
]


@pytest.fixture
def admin(client, api_users, seeded):  # type: ignore[no-untyped-def]
    assert login_as(client, "admin@acme.test").status_code == 200
    return client


@pytest.mark.parametrize("template", SEARCHES)
def test_hostile_search_text_never_causes_an_internal_error(admin, template: str) -> None:  # type: ignore[no-untyped-def]
    for text in HOSTILE:
        response = admin.get(template.format(""), params={template.split("?")[1].split("=")[0]: text})
        assert response.status_code in (200, 400, 422), (template, text, response.status_code, response.text[:200])
        body = response.text
        for leak in ("Traceback", "sqlalchemy", "psycopg", "site-packages", "SELECT ", "pg_catalog"):
            assert leak not in body, (template, text, leak)


def test_an_injection_string_matches_nothing_instead_of_everything(admin) -> None:  # type: ignore[no-untyped-def]
    everything = admin.get("/api/v1/admin/users").json()["total"]
    assert everything >= 3
    for text in ("' OR '1'='1", "x' OR 1=1 --", "%' OR '%'='"):
        assert admin.get("/api/v1/admin/users", params={"q": text}).json()["total"] == 0


def test_an_ordinary_user_cannot_make_themselves_an_administrator(client, api_users) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert client.patch(f"/api/v1/admin/users/{me['user_id']}", json={"role": "admin"}).status_code == 403
    assert client.post("/api/v1/admin/users", json={"email": "x@acme.test", "full_name": "X", "role": "admin", "password": "Correct-Horse-9-Battery"}).status_code == 403
    assert client.get("/api/v1/auth/me").json()["role"] == "analyst"


def test_extra_fields_in_a_request_cannot_move_data_to_another_organization(client, api_users, seeded) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    source = client.post("/api/v1/sources/upload", files={"file": ("p.csv", b"id,name\n1,a\n")}).json()
    created = client.post("/api/v1/jobs", json={"source_id": source["id"], "target_schema": "commerce", "org_id": str(api_users.other_org_id), "created_by": "00000000-0000-4000-8000-000000000000", "status": "COMPLETED"})
    assert created.status_code == 201
    job = created.json()
    assert job["status"] == "CREATED"
    me = client.get("/api/v1/auth/me").json()
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["owner_name"] == me["full_name"]
    assert login_as(client, "admin@globex.test").status_code == 200
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404  # the other organization cannot see it


@pytest.mark.parametrize("name", ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "%00", "COMMERCE", "commerce%20", "x" * 300])
def test_a_sample_name_is_never_used_as_a_path(client, api_users, name: str) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    assert client.post(f"/api/v1/sources/samples/{name}").status_code in (400, 404, 422)


def test_errors_do_not_show_how_the_server_is_built(client, api_users) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    for response in (
        client.get("/api/v1/jobs/not-a-uuid"),
        client.get("/api/v1/jobs/00000000-0000-4000-8000-000000000000"),
        client.post("/api/v1/jobs", content=b"{not json", headers={"Content-Type": "application/json"}),
        client.get("/api/v1/no-such-route"),
        client.put("/api/v1/jobs"),
    ):
        assert 400 <= response.status_code < 500
        for leak in ("Traceback", "File \"", "site-packages", "sqlalchemy", "psycopg", "D:\\", "/app/"):
            assert leak not in response.text, (response.status_code, leak)
        assert "error" in response.json()


def test_a_forged_or_stripped_token_is_not_a_session(client, api_users) -> None:  # type: ignore[no-untyped-def]
    import base64
    import json

    assert login_as(client, "analyst@acme.test").status_code == 200
    token = client.cookies.get("access_token") or next(c.value for c in client.cookies.jar if "access" in c.name)
    head, body, _ = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    claims = json.loads(base64.urlsafe_b64decode(pad(body)))
    claims["role"] = "admin"
    forged_body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    none_head = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    for candidate in (f"{head}.{forged_body}.AAAA", f"{none_head}.{forged_body}.", f"{none_head}.{body}.", "not.a.token", ""):
        client.cookies.clear()
        client.cookies.set("access_token", candidate)
        assert client.get("/api/v1/auth/me").status_code == 401, candidate[:20]
