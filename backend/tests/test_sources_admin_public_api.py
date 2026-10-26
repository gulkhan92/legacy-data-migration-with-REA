"""Sources (get, delete, samples), admin users and leads, the public endpoints, and the shape of errors."""

import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.enums import Role
from tests.conftest import PASSWORD, login_as, make_user
from tests.test_jobs_api import SQLITE_BYTES, make_job
from tests.test_mapping_api import seeded  # noqa: F401


def as_(client, email: str) -> None:  # type: ignore[no-untyped-def]
    client.cookies.clear()
    assert login_as(client, email).status_code == 200


# ------------------------------------------------------------------ sources


def test_source_get_and_delete(client, api_users, seeded) -> None:  # type: ignore[no-untyped-def]
    as_(client, "analyst@acme.test")
    uploaded = client.post("/api/v1/sources/upload", files={"file": ("old.db", SQLITE_BYTES)}).json()
    assert client.get(f"/api/v1/sources/{uploaded['id']}").json()["name"] == "old.db"
    assert client.delete(f"/api/v1/sources/{uploaded['id']}").status_code == 204
    assert client.get(f"/api/v1/sources/{uploaded['id']}").status_code == 404
    assert client.delete(f"/api/v1/sources/{uploaded['id']}").status_code == 404

    # a source that a job uses cannot be deleted until the job is gone
    job = make_job(client)
    used = client.get(f"/api/v1/jobs/{job}").json()["source_id"]
    refused = client.delete(f"/api/v1/sources/{used}")
    assert refused.status_code == 409 and "job" in refused.json()["detail"]


def test_deleting_a_source_removes_its_encrypted_file(client, api_users, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    as_(client, "analyst@acme.test")
    uploaded = client.post("/api/v1/sources/upload", files={"file": ("gone.db", SQLITE_BYTES)}).json()
    files = list(Path(get_settings().upload_dir).rglob("*.enc"))
    assert len(files) >= 1
    client.delete(f"/api/v1/sources/{uploaded['id']}")
    assert len(list(Path(get_settings().upload_dir).rglob("*.enc"))) == len(files) - 1


def test_sources_are_private_and_deleting_needs_analyst(client, api_users) -> None:  # type: ignore[no-untyped-def]
    as_(client, "analyst@acme.test")
    source = client.post("/api/v1/sources/upload", files={"file": ("p.db", SQLITE_BYTES)}).json()["id"]
    as_(client, "viewer@acme.test")
    assert client.delete(f"/api/v1/sources/{source}").status_code == 403
    assert client.post("/api/v1/sources/samples/commerce").status_code == 403
    as_(client, "admin@globex.test")
    assert client.get(f"/api/v1/sources/{source}").status_code == 404
    assert client.delete(f"/api/v1/sources/{source}").status_code == 404


def test_sample_systems(client, api_users) -> None:  # type: ignore[no-untyped-def]
    as_(client, "analyst@acme.test")
    samples = {s["name"]: s for s in client.get("/api/v1/sources/samples").json()}
    assert set(samples) == {"commerce", "clinic"} and samples["commerce"]["available"] and samples["clinic"]["files"] == ["ClinicDesk.dll", "clinic.cdb"]
    commerce = client.post("/api/v1/sources/samples/commerce")
    assert commerce.status_code == 201, commerce.text
    body = commerce.json()
    assert body["target_schema"] == "commerce" and body["data_source_id"] is None and body["sources"][0]["is_sample"] is True
    assert client.post("/api/v1/sources/samples/commerce").json()["source_id"] == body["source_id"]  # registering again returns the same source
    clinic = client.post("/api/v1/sources/samples/clinic").json()
    assert len(clinic["sources"]) == 2 and clinic["data_source_id"] and clinic["source_id"] != clinic["data_source_id"]
    assert [s["kind"] for s in clinic["sources"]] == ["dotnet_binary", "dotnet_binary"]
    assert client.post("/api/v1/sources/samples/nope").status_code == 404
    as_(client, "viewer@acme.test")
    assert client.get("/api/v1/sources/samples").status_code == 200


# ------------------------------------------------------------------ admin users


def new_user(client, email: str, role: str = "analyst", password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/admin/users", json={"email": email, "full_name": "Test Person", "role": role, "password": password})


def test_admin_creates_lists_changes_and_removes_users(client, api_users) -> None:  # type: ignore[no-untyped-def]
    as_(client, "admin@acme.test")
    email = f"new-{uuid.uuid4().hex[:6]}@acme.test"
    created = new_user(client, email, "viewer")
    assert created.status_code == 201, created.text
    user = created.json()
    assert user["role"] == "viewer" and user["email"] == email and "password" not in str(user)

    listing = client.get("/api/v1/admin/users", params={"q": email[:8]}).json()
    assert set(listing) == {"items", "total", "limit", "offset"} and [u["email"] for u in listing["items"]] == [email]
    assert {u["role"] for u in client.get("/api/v1/admin/users?role=admin").json()["items"]} == {"admin"}
    emails = {u["email"] for u in client.get("/api/v1/admin/users?limit=200").json()["items"]}
    assert {"admin@acme.test", "analyst@acme.test", "viewer@acme.test", email} <= emails
    assert "admin@globex.test" not in emails  # the other organization is invisible

    # the new account can sign in, with the role it was given
    as_(client, email)
    assert client.get("/api/v1/auth/me").json()["role"] == "viewer"
    as_(client, "admin@acme.test")

    changed = client.patch(f"/api/v1/admin/users/{user['user_id']}", json={"role": "analyst"})
    assert changed.status_code == 200 and changed.json()["role"] == "analyst"
    assert client.patch(f"/api/v1/admin/users/{user['user_id']}", json={"role": "emperor"}).status_code == 422
    assert client.patch(f"/api/v1/admin/users/{uuid.uuid4()}", json={"role": "viewer"}).status_code == 404

    assert client.delete(f"/api/v1/admin/users/{user['user_id']}").status_code == 204
    assert email not in {u["email"] for u in client.get("/api/v1/admin/users?limit=200").json()["items"]}
    assert client.delete(f"/api/v1/admin/users/{user['user_id']}").status_code == 404
    actions = [e["action"] for e in client.get("/api/v1/admin/audit-log?limit=50").json()]
    assert {"user.create", "user.role_change", "user.remove"} <= set(actions)


def test_user_creation_rules(client, api_users) -> None:  # type: ignore[no-untyped-def]
    as_(client, "admin@acme.test")
    weak = new_user(client, f"weak-{uuid.uuid4().hex[:6]}@acme.test", password="short")
    assert weak.status_code == 422 and "password" in weak.json()["detail"]
    assert new_user(client, "not-an-email").status_code == 422
    # the same refusal for an address of this organization and of another one
    mine = new_user(client, "viewer@acme.test")
    theirs = new_user(client, "admin@globex.test")
    assert mine.status_code == theirs.status_code == 409 and mine.json()["detail"] == theirs.json()["detail"]
    assert new_user(client, f"x-{uuid.uuid4().hex[:6]}@acme.test", role="emperor").status_code == 422


def test_the_last_admin_and_yourself_cannot_be_removed(client, api_users, admin_engine) -> None:  # type: ignore[no-untyped-def]
    # an organization of its own, so changing its admins cannot disturb the users other tests rely on
    with Session(admin_engine) as session:
        org = models.Organization(name="Solo", slug=f"solo-{uuid.uuid4().hex[:6]}")
        session.add(org)
        session.commit()
        org_id = org.id
    email = f"solo-{uuid.uuid4().hex[:6]}@solo.test"
    make_user(admin_engine, email, Role.ADMIN, org_id)
    as_(client, email)  # the only admin of its organization
    me = client.get("/api/v1/auth/me").json()["user_id"]
    assert client.delete(f"/api/v1/admin/users/{me}").status_code == 409
    assert client.patch(f"/api/v1/admin/users/{me}", json={"role": "viewer"}).status_code == 409
    other = new_user(client, f"second-{uuid.uuid4().hex[:6]}@solo.test", "admin").json()
    assert client.patch(f"/api/v1/admin/users/{me}", json={"role": "viewer"}).status_code == 200  # now there is another admin
    as_(client, other["email"])
    assert client.delete(f"/api/v1/admin/users/{other['user_id']}").status_code == 409  # you cannot remove yourself


def test_removing_a_user_ends_their_sessions(client, api_users) -> None:  # type: ignore[no-untyped-def]
    as_(client, "admin@acme.test")
    email = f"temp-{uuid.uuid4().hex[:6]}@acme.test"
    user = new_user(client, email, "viewer").json()
    as_(client, email)
    assert client.post("/api/v1/auth/refresh").status_code == 200
    as_(client, email)  # a fresh login so the refresh cookie is valid again
    cookies = dict(client.cookies)
    as_(client, "admin@acme.test")
    assert client.delete(f"/api/v1/admin/users/{user['user_id']}").status_code == 204
    client.cookies.clear()
    for name, value in cookies.items():
        client.cookies.set(name, value)
    assert client.post("/api/v1/auth/refresh").status_code == 401


# ------------------------------------------------------------------ public


def test_public_metrics_are_aggregates_and_cacheable(client, admin_engine) -> None:  # type: ignore[no-untyped-def]
    empty = client.get("/api/v1/public/metrics")
    assert empty.status_code == 200 and empty.headers["cache-control"] == "public, max-age=300"
    with admin_engine.begin() as connection:  # keep what other tests may have written (the billing history) and put it back afterwards
        connection.execute(text("DROP TABLE IF EXISTS daily_metrics_backup"))
        connection.execute(text("CREATE TABLE daily_metrics_backup AS TABLE daily_metrics"))
        connection.execute(text("DELETE FROM daily_metrics"))
    try:
        with Session(admin_engine) as session:
            session.add_all([
                models.DailyMetric(metric_date=date(2026, 10, 20), active_orgs=10, jobs=100, records=1_000_000, success_rate=0.9, quality_score=95, avg_duration_ms=40_000, mrr_cents=12345),
                models.DailyMetric(metric_date=date(2026, 10, 21), active_orgs=12, jobs=50, records=500_000, success_rate=1.0, quality_score=97, avg_duration_ms=20_000, mrr_cents=99999),
            ])
            session.commit()
        body = client.get("/api/v1/public/metrics").json()
        assert body == {"demo": True, "as_of": "2026-10-21", "days": 2, "organizations": 12, "jobs_run": 150, "records_migrated": 1_500_000, "success_rate": 0.95,
                        "average_quality_score": 96.0, "average_duration_seconds": 30.0}  # fmt: skip
        assert "mrr" not in str(body).lower()  # revenue stays private
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text("DELETE FROM daily_metrics"))
            connection.execute(text("INSERT INTO daily_metrics SELECT * FROM daily_metrics_backup"))
            connection.execute(text("DROP TABLE daily_metrics_backup"))


def test_leads_are_stored_and_only_admins_read_them(client, api_users) -> None:  # type: ignore[no-untyped-def]
    marker = uuid.uuid4().hex[:8]
    sent = client.post("/api/v1/public/leads", json={"name": "Grace", "email": f"grace-{marker}@example.com", "company": "Example", "message": "Demo please", "kind": "demo"})
    assert sent.status_code == 201 and sent.json() == {"received": True}
    news = client.post("/api/v1/public/leads", json={"email": f"news-{marker}@example.com", "kind": "newsletter"})
    assert news.status_code == 201  # the footer field of the public site
    spam = client.post("/api/v1/public/leads", json={"email": f"bot-{marker}@example.com", "website": "http://spam.example"})
    assert spam.status_code == 201  # the bot is told it worked
    assert client.post("/api/v1/public/leads", json={"email": "nope"}).status_code == 422
    assert client.post("/api/v1/public/leads", json={"email": "a@b.co", "kind": "other"}).status_code == 422
    assert client.post("/api/v1/public/leads", json={"email": "a@b.co", "message": "x" * 4001}).status_code == 422
    assert client.post("/api/v1/public/leads", json={"email": "a@b.co", "role": "admin"}).status_code == 422  # unknown fields are refused
    client.cookies.clear()
    assert client.get("/api/v1/admin/leads").status_code == 401
    as_(client, "analyst@acme.test")
    assert client.get("/api/v1/admin/leads").status_code == 403
    as_(client, "admin@acme.test")
    leads = client.get("/api/v1/admin/leads?kind=demo&limit=200").json()
    assert f"grace-{marker}@example.com" in {l["email"] for l in leads["items"]}
    assert f"news-{marker}@example.com" in {l["email"] for l in client.get("/api/v1/admin/leads?kind=newsletter&limit=200").json()["items"]}
    assert f"bot-{marker}@example.com" not in {l["email"] for l in client.get("/api/v1/admin/leads?limit=200").json()["items"]}


def test_public_endpoints_still_need_the_csrf_header(client) -> None:  # type: ignore[no-untyped-def]
    client.headers.pop("X-Requested-With", None)
    assert client.post("/api/v1/public/leads", json={"email": "a@b.co"}).status_code == 403
    assert client.get("/api/v1/public/metrics").status_code == 200


def test_the_lead_form_is_rate_limited(client) -> None:  # type: ignore[no-untyped-def]
    from app.core.rate_limit import limiter

    limiter.enabled = True
    limiter.reset()
    try:
        codes = [client.post("/api/v1/public/leads", json={"email": f"r{i}@example.com"}).status_code for i in range(7)]
    finally:
        limiter.enabled = False
        limiter.reset()
    assert codes[:5] == [201] * 5 and codes[5:] == [429, 429]


# ------------------------------------------------------------------ errors


def test_errors_share_one_shape_and_never_echo_input(client, api_users) -> None:  # type: ignore[no-untyped-def]
    missing = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert missing.status_code == 401 and missing.json()["error"] == {"code": "not_authenticated", "message": "Not authenticated", "status": 401}
    assert missing.json()["detail"] == "Not authenticated"  # the original FastAPI field is kept

    bad = client.post("/api/v1/auth/login", json={"email": "someone@acme.test", "password": 12345, "extra": "Correct-Horse-9-Battery"})
    assert bad.status_code == 422
    body = bad.json()
    assert body["error"]["code"] == "validation_error" and body["error"]["fields"] and all({"field", "message", "type"} <= set(f) for f in body["error"]["fields"])
    assert "12345" not in bad.text and "Correct-Horse" not in bad.text  # no submitted value comes back
    assert all("input" not in d for d in body["detail"])

    as_(client, "analyst@acme.test")
    assert client.get(f"/api/v1/jobs/{uuid.uuid4()}").json()["error"]["code"] == "not_found"
    assert client.get("/api/v1/jobs/not-a-uuid").json()["error"]["code"] == "validation_error"
    assert client.get("/api/v1/admin/users").json()["error"]["code"] == "forbidden"
    assert client.put("/api/v1/jobs").json()["error"]["code"] == "method_not_allowed"
    refused = client.post("/api/v1/jobs/" + str(uuid.uuid4()) + "/rollback")
    assert refused.json()["error"]["status"] == refused.status_code


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_openapi_is_served(client, path) -> None:  # type: ignore[no-untyped-def]
    assert client.get(path).status_code == 200
