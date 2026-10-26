"""The endpoints of the console: overview, notifications, mapping memory, job filters, audit search, organization switch, retention."""

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db.models.enums import JobStatus, LlmCallStatus, Role, SourceKind
from app.db.session import scoped_session
from app.services import retention
from tests.conftest import PASSWORD, login_as, make_user
from tests.test_jobs_api import SQLITE_BYTES, make_job
from tests.test_mapping_api import seeded  # noqa: F401

NOW = datetime.now(UTC)


@pytest.fixture
def analyst(client, api_users, seeded):  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    return client


@pytest.fixture
def cleanup(admin_engine):  # type: ignore[no-untyped-def]
    """Rows a test adds to the shared organization are removed afterwards."""
    jobs: list[uuid.UUID] = []
    yield jobs
    with admin_engine.begin() as connection:
        if jobs:
            connection.execute(text("DELETE FROM usage_events WHERE job_id = ANY(:j)"), {"j": jobs})
            connection.execute(text("DELETE FROM llm_calls WHERE job_id = ANY(:j)"), {"j": jobs})
            connection.execute(text("DELETE FROM jobs WHERE id = ANY(:j)"), {"j": jobs})


def add_job(engine, org: uuid.UUID, status: JobStatus, *, completed: datetime | None = None, records: int = 0, tokens: int = 0, duration_ms: int = 1000, name: str = "System", kind: SourceKind = SourceKind.SQLITE, owner=None) -> uuid.UUID:  # type: ignore[no-untyped-def]
    with Session(engine) as session:
        source = models.Source(org_id=org, name=name, kind=kind)
        session.add(source)
        session.flush()
        job = models.Job(org_id=org, source_id=source.id, status=status, tokens_used=tokens, created_by=owner, started_at=completed, completed_at=completed)
        session.add(job)
        session.flush()
        if status is JobStatus.COMPLETED and completed is not None:
            session.add(models.UsageEvent(org_id=org, job_id=job.id, records_loaded=records, tokens_used=tokens, duration_ms=duration_ms, occurred_at=completed, is_simulated=False))
        if tokens:
            session.add(models.LlmCall(org_id=org, job_id=job.id, provider="groq", model="m", task="t", tokens_in=tokens, tokens_out=0, status=LlmCallStatus.OK))
        session.commit()
        return job.id


# ------------------------------------------------------------------ overview


def test_the_overview_adds_up_this_months_jobs(analyst, api_users, admin_engine, cleanup) -> None:  # type: ignore[no-untyped-def]
    today = NOW.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    if today > NOW:
        today = NOW - timedelta(hours=1)
    before = analyst.get("/api/v1/overview").json()["kpis"]
    for ok, records, ms, tokens in [(True, 1_000, 2_000, 300), (True, 3_000, 4_000, 0), (False, 0, 0, 0)]:
        cleanup.append(add_job(admin_engine, api_users.org_id, JobStatus.COMPLETED if ok else JobStatus.FAILED, completed=today, records=records, duration_ms=ms, tokens=tokens))
    body = analyst.get("/api/v1/overview").json()
    k = body["kpis"]
    assert k["jobs"] == before["jobs"] + 3 and k["records"] == before["records"] + 4_000 and k["tokens"] == before["tokens"] + 300
    assert 0 < k["success_rate"] < 1 and k["avg_duration_ms"] is not None
    assert set(body) == {"month", "kpis", "previous", "in_progress", "recent", "total_jobs"} and body["month"]["from"].endswith("-01")


def test_the_overview_lists_jobs_in_progress_and_recent_ones_with_their_source(analyst, api_users, admin_engine, cleanup) -> None:  # type: ignore[no-untyped-def]
    running = add_job(admin_engine, api_users.org_id, JobStatus.DISCOVERING, name="Running system", kind=SourceKind.DOTNET_BINARY)
    done = add_job(admin_engine, api_users.org_id, JobStatus.COMPLETED, completed=NOW - timedelta(hours=1), records=10, name="Finished system")
    cleanup.extend([running, done])
    body = analyst.get("/api/v1/overview").json()
    active = {j["id"]: j for j in body["in_progress"]}
    assert str(running) in active and str(done) not in active
    assert active[str(running)]["source_name"] == "Running system" and active[str(running)]["source_kind"] == "dotnet_binary"
    assert str(done) in {j["id"] for j in body["recent"]} and body["total_jobs"] >= 2


def test_the_overview_is_private_to_the_organization(client, analyst, api_users, admin_engine, cleanup) -> None:  # type: ignore[no-untyped-def]
    cleanup.append(add_job(admin_engine, api_users.org_id, JobStatus.DISCOVERING, name="Acme only"))
    client.cookies.clear()
    assert login_as(client, "admin@globex.test").status_code == 200
    body = client.get("/api/v1/overview").json()
    assert body["in_progress"] == [] and body["recent"] == [] and body["total_jobs"] == 0 and body["kpis"]["jobs"] == 0


# ------------------------------------------------------------------ jobs: filters and sorting


def test_the_job_list_carries_source_and_owner_and_filters(analyst, api_users, admin_engine, cleanup) -> None:  # type: ignore[no-untyped-def]
    me = analyst.get("/api/v1/auth/me").json()["user_id"]
    old = add_job(admin_engine, api_users.org_id, JobStatus.COMPLETED, completed=NOW, name="Zeta shop", owner=uuid.UUID(me))
    binary = add_job(admin_engine, api_users.org_id, JobStatus.FAILED, completed=NOW, name="Alpha clinic", kind=SourceKind.DOTNET_BINARY)
    cleanup.extend([old, binary])
    with Session(admin_engine) as session:
        session.execute(text("UPDATE jobs SET created_at = :c WHERE id = :i"), {"c": NOW - timedelta(days=40), "i": old})
        session.commit()
    items = {j["id"]: j for j in analyst.get("/api/v1/jobs?limit=200").json()["items"]}
    assert items[str(old)]["source_name"] == "Zeta shop" and items[str(old)]["owner_name"] and items[str(old)]["source_kind"] == "sqlite"
    ids = lambda query: {j["id"] for j in analyst.get(f"/api/v1/jobs?limit=200&{query}").json()["items"]}  # noqa: E731
    assert str(binary) in ids("source_kind=dotnet_binary") and str(old) not in ids("source_kind=dotnet_binary")
    assert ids("q=zeta") >= {str(old)} and str(binary) not in ids("q=zeta")
    assert str(old) not in ids(f"created_from={(NOW - timedelta(days=10)).date()}") and str(old) in ids(f"created_to={(NOW - timedelta(days=30)).date()}")
    assert str(old) in ids(f"owner_id={me}") and str(binary) not in ids(f"owner_id={me}")
    names = lambda order: [j["source_name"] for j in analyst.get(f"/api/v1/jobs?limit=200&sort=source&order={order}").json()["items"]]  # noqa: E731
    assert names("asc").index("Alpha clinic") < names("asc").index("Zeta shop") and names("desc").index("Zeta shop") < names("desc").index("Alpha clinic")
    assert analyst.get("/api/v1/jobs?sort=colour").status_code == 422 and analyst.get("/api/v1/jobs?order=up").status_code == 422
    assert analyst.get("/api/v1/jobs?q=%25").status_code == 200  # wildcards are neutralised


# ------------------------------------------------------------------ notifications


def test_notifications_show_what_needs_attention_and_not_the_routine(analyst, api_users, admin_engine, cleanup) -> None:  # type: ignore[no-untyped-def]
    job = add_job(admin_engine, api_users.org_id, JobStatus.CREATED, name="Notifying")
    cleanup.append(job)
    rows = [("CREATED", "UPLOADED", "Source uploaded"), ("UPLOADED", "UPLOADED", "Stage discover started"), ("MAPPED", "AWAITING_REVIEW", "Rules generated"),
            ("AWAITING_REVIEW", "AWAITING_REVIEW", "Stage dry_run is waiting: needs a code map"), ("READY", "LOADING", "Loading"), ("RECONCILING", "COMPLETED", "Finished"), ("LOADING", "FAILED", "Failed")]  # fmt: skip
    with Session(admin_engine) as session:
        for frm, to, message in rows:
            session.add(models.JobEvent(org_id=api_users.org_id, job_id=job, from_status=frm, to_status=to, message=message, detail={}))
        session.commit()
    items = [n for n in analyst.get("/api/v1/notifications?limit=100").json() if n["job_id"] == str(job)]
    assert {n["message"] for n in items} == {"Rules generated", "Stage dry_run is waiting: needs a code map", "Finished", "Failed"}
    kinds = {n["message"]: n["kind"] for n in items}
    assert kinds == {"Rules generated": "action", "Stage dry_run is waiting: needs a code map": "waiting", "Finished": "success", "Failed": "error"}
    assert all(n["source_name"] == "Notifying" for n in items)
    newest = max(n["id"] for n in items)
    assert [n for n in analyst.get(f"/api/v1/notifications?after={newest}").json() if n["job_id"] == str(job)] == []
    assert len(analyst.get("/api/v1/notifications?limit=1").json()) <= 1 and analyst.get("/api/v1/notifications?limit=500").status_code == 422


# ------------------------------------------------------------------ mapping memory


@pytest.fixture
def memory(admin_engine, api_users, seeded):  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        rows = session.execute(text(
            "SELECT id, table_name, name, schema FROM (SELECT f.id, f.table_name, f.name, s.name AS schema, row_number() OVER (PARTITION BY s.name ORDER BY f.name) AS n "
            "FROM target_fields f JOIN target_schemas s ON s.id = f.schema_id WHERE s.name IN ('commerce', 'clinic')) x WHERE n <= 3 ORDER BY schema, name")).all()
        ids = []
        for n, (field_id, *_rest) in enumerate(rows):
            m = models.MappingMemory(org_id=api_users.org_id, source_signature=f"console-test-{uuid.uuid4().hex[:8]}-{n}", source_text=f"table sample | column col{n} | email marker{n}", target_field_id=field_id, times_approved=n + 1)
            session.add(m)
            session.flush()
            ids.append(m.id)
        session.commit()
    yield rows, ids
    with admin_engine.begin() as connection:
        connection.execute(text("DELETE FROM mapping_memory WHERE id = ANY(:i)"), {"i": ids})


def test_the_mapping_memory_is_listed_searched_and_paged(analyst, memory) -> None:  # type: ignore[no-untyped-def]
    rows, ids = memory
    page = analyst.get("/api/v1/mapping-memory?limit=200").json()
    mine = [i for i in page["items"] if i["id"] in {str(x) for x in ids}]
    assert len(mine) == len(ids) and set(page) == {"items", "total", "limit", "offset"}
    assert all("." in i["target"] and i["target_schema"] in {"commerce", "clinic"} and i["times_approved"] >= 1 for i in mine)
    assert [i["id"] for i in analyst.get("/api/v1/mapping-memory?q=marker2&limit=200").json()["items"]] == [str(ids[2])]
    commerce = analyst.get("/api/v1/mapping-memory?schema=commerce&limit=200").json()["items"]
    assert commerce and {i["target_schema"] for i in commerce} == {"commerce"}
    assert analyst.get("/api/v1/mapping-memory?limit=1").json()["limit"] == 1 and len(analyst.get("/api/v1/mapping-memory?limit=1").json()["items"]) == 1


def test_a_remembered_mapping_can_be_forgotten_by_an_analyst_only(client, analyst, memory) -> None:  # type: ignore[no-untyped-def]
    _, ids = memory
    assert analyst.delete(f"/api/v1/mapping-memory/{ids[0]}").status_code == 204
    assert analyst.delete(f"/api/v1/mapping-memory/{ids[0]}").status_code == 404
    client.cookies.clear()
    assert login_as(client, "viewer@acme.test").status_code == 200
    assert client.delete(f"/api/v1/mapping-memory/{ids[1]}").status_code == 403
    assert str(ids[1]) in {i["id"] for i in client.get("/api/v1/mapping-memory?limit=200").json()["items"]}
    client.cookies.clear()
    assert login_as(client, "admin@globex.test").status_code == 200
    assert client.get("/api/v1/mapping-memory").json()["total"] == 0 and client.delete(f"/api/v1/mapping-memory/{ids[1]}").status_code == 404  # another organization's
    client.cookies.clear()
    assert login_as(client, "admin@acme.test").status_code == 200
    assert "mapping.memory_delete" in {e["action"] for e in client.get("/api/v1/admin/audit-log?limit=100").json()}


# ------------------------------------------------------------------ audit search


def test_the_audit_log_is_searchable_and_counts_its_matches(client, api_users) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "admin@acme.test").status_code == 200
    marker = uuid.uuid4().hex[:8]
    created = client.post("/api/v1/admin/users", json={"email": f"audit-{marker}@acme.test", "full_name": "Audit Subject", "role": "viewer", "password": PASSWORD})
    assert created.status_code == 201
    everything = client.get("/api/v1/admin/audit-log?limit=5")
    assert int(everything.headers["x-total-count"]) >= 2 and len(everything.json()) == 5
    found = client.get("/api/v1/admin/audit-log", params={"q": "user.create"})
    assert found.status_code == 200 and found.json() and all(e["action"] == "user.create" for e in found.json())
    assert int(found.headers["x-total-count"]) >= len(found.json())
    assert any(e["detail"].get("role") == "viewer" for e in client.get("/api/v1/admin/audit-log", params={"q": "viewer"}).json())
    me = client.get("/api/v1/auth/me").json()
    mine = client.get("/api/v1/admin/audit-log", params={"user_id": me["user_id"], "limit": 200}).json()
    assert mine and {e["user_id"] for e in mine} == {me["user_id"]} and mine[0]["user_name"]
    today = date.today().isoformat()
    assert client.get("/api/v1/admin/audit-log", params={"from": today, "to": today}).json()
    assert client.get("/api/v1/admin/audit-log", params={"to": "2001-01-01"}).json() == []
    assert client.get("/api/v1/admin/audit-log", params={"q": "zzzz-nothing-" + marker}).json() == []
    assert client.get("/api/v1/admin/audit-log?limit=0").status_code == 422
    assert client.get("/api/v1/admin/audit-log", params={"q": "%_"}).status_code == 200


# ------------------------------------------------------------------ organizations


@pytest.fixture
def traveller(admin_engine, api_users):  # type: ignore[no-untyped-def]
    """One user who belongs to both organizations, an analyst in the first and an admin in the second."""
    email = f"travel-{uuid.uuid4().hex[:6]}@acme.test"
    user_id = make_user(admin_engine, email, Role.ANALYST, api_users.org_id)
    with Session(admin_engine) as session:
        session.add(models.Membership(org_id=api_users.other_org_id, user_id=user_id, role=Role.ADMIN))
        session.commit()
    yield email
    with admin_engine.begin() as connection:
        connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})


def test_a_user_lists_and_switches_between_their_organizations(client, api_users, traveller) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, traveller).status_code == 200
    orgs = client.get("/api/v1/auth/organizations").json()
    assert {(o["name"], o["role"], o["current"]) for o in orgs} == {("Acme", "analyst", True), ("Globex", "admin", False)}
    first_refresh = client.cookies.get("refresh_token", path="/api/v1/auth")
    switched = client.post("/api/v1/auth/switch", json={"org_id": str(api_users.other_org_id)})
    assert switched.status_code == 200 and switched.json()["org_name"] == "Globex" and switched.json()["role"] == "admin"
    me = client.get("/api/v1/auth/me").json()
    assert me["org_name"] == "Globex" and me["role"] == "admin"
    assert client.get("/api/v1/admin/users").status_code == 200  # the new role applies at once
    assert {(o["name"], o["current"]) for o in client.get("/api/v1/auth/organizations").json()} == {("Acme", False), ("Globex", True)}
    # the switch is in the audit log of the organization that was entered
    assert "auth.org_switch" in {e["action"] for e in client.get("/api/v1/admin/audit-log?limit=50").json()}
    assert client.cookies.get("refresh_token", path="/api/v1/auth") != first_refresh  # new tokens, the old refresh token is revoked
    client.cookies.set("refresh_token", first_refresh, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_switching_to_an_organization_you_do_not_belong_to_is_refused(client, api_users) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    assert client.post("/api/v1/auth/switch", json={"org_id": str(api_users.other_org_id)}).status_code == 403
    assert client.post("/api/v1/auth/switch", json={"org_id": str(uuid.uuid4())}).status_code == 403
    assert client.post("/api/v1/auth/switch", json={"org_id": "not-a-uuid"}).status_code == 422
    assert client.get("/api/v1/auth/me").json()["org_name"] == "Acme"  # still where they were
    assert [o["name"] for o in client.get("/api/v1/auth/organizations").json()] == ["Acme"]  # and the other organization's name is not revealed
    client.cookies.clear()
    assert client.get("/api/v1/auth/organizations").status_code == 401


# ------------------------------------------------------------------ retention


def test_the_retention_setting_is_validated_audited_and_for_admins(client, api_users) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    assert client.get("/api/v1/admin/retention").status_code == 403
    client.cookies.clear()
    assert login_as(client, "admin@globex.test").status_code == 200
    assert client.get("/api/v1/admin/retention").json()["retention_days"] == 365
    assert client.put("/api/v1/admin/retention", json={"retention_days": 90}).json()["retention_days"] == 90
    assert client.get("/api/v1/admin/retention").json()["retention_days"] == 90
    for bad in (3, 4000, "soon"):
        assert client.put("/api/v1/admin/retention", json={"retention_days": bad}).status_code == 422
    assert any(e["action"] == "settings.retention_change" and e["detail"] == {"from": 365, "to": 90} for e in client.get("/api/v1/admin/audit-log?limit=20").json())
    assert client.put("/api/v1/admin/retention", json={"retention_days": 365}).status_code == 200  # put it back for other tests


def upload_old(client, admin_engine, name: str, *, age_days: int) -> tuple[str, Path]:  # type: ignore[no-untyped-def]
    uploaded = client.post("/api/v1/sources/upload", files={"file": (name, SQLITE_BYTES)}).json()["id"]
    with Session(admin_engine) as session:
        path = session.execute(text("SELECT storage_path FROM sources WHERE id = :i"), {"i": uploaded}).scalar_one()
        session.execute(text("UPDATE sources SET created_at = :c WHERE id = :i"), {"c": NOW - timedelta(days=age_days), "i": uploaded})
        session.commit()
    return uploaded, Path(get_settings().upload_dir) / path


def test_the_nightly_purge_deletes_only_expired_files_of_finished_work(client, api_users, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    old_unused, old_unused_file = upload_old(client, admin_engine, "unused.db", age_days=800)
    old_finished, old_finished_file = upload_old(client, admin_engine, "finished.db", age_days=800)
    old_running, old_running_file = upload_old(client, admin_engine, "running.db", age_days=800)
    old_recent_job, old_recent_file = upload_old(client, admin_engine, "recent.db", age_days=800)
    young, young_file = upload_old(client, admin_engine, "young.db", age_days=3)
    with Session(admin_engine) as session:
        def job(source: str, status: str, updated_days_ago: int) -> None:
            session.add(models.Job(org_id=api_users.org_id, source_id=uuid.UUID(source), status=status))
            session.flush()
            session.execute(text("UPDATE jobs SET updated_at = :u WHERE source_id = :s"), {"u": NOW - timedelta(days=updated_days_ago), "s": source})
        job(old_finished, "COMPLETED", 700)
        job(old_running, "DISCOVERING", 700)
        job(old_recent_job, "COMPLETED", 5)
        session.commit()
    result = retention.purge_expired_uploads(now=NOW, factory=app_factory)
    assert result["files_deleted"] >= 2
    assert not old_unused_file.exists() and not old_finished_file.exists()  # old, and nothing is using them
    assert old_running_file.exists() and old_recent_file.exists() and young_file.exists()  # a running job, recent activity, and a young upload all keep their file
    with Session(admin_engine) as session:
        paths = dict(session.execute(text("SELECT id::text, storage_path FROM sources WHERE id = ANY(:i)"), {"i": [uuid.UUID(x) for x in (old_unused, old_finished, old_running, old_recent_job, young)]}).all())
        assert paths[old_unused] is None and paths[old_finished] is None and all(paths[k] for k in (old_running, old_recent_job, young))
        assert session.execute(text("SELECT count(*) FROM audit_log WHERE action = 'retention.purge' AND org_id = :o"), {"o": api_users.org_id}).scalar() >= 1
    assert retention.purge_expired_uploads(now=NOW, factory=app_factory)["files_deleted"] == 0  # nothing left to do: running it again changes nothing
    with admin_engine.begin() as connection:
        connection.execute(text("DELETE FROM jobs WHERE source_id = ANY(:s)"), {"s": [uuid.UUID(old_finished), uuid.UUID(old_running), uuid.UUID(old_recent_job)]})
        connection.execute(text("DELETE FROM sources WHERE id = ANY(:s)"), {"s": [uuid.UUID(x) for x in (old_unused, old_finished, old_running, old_recent_job, young)]})


def test_a_shorter_retention_period_reaches_younger_files(client, api_users, admin_engine, app_factory) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "admin@globex.test").status_code == 200
    source, file = upload_old(client, admin_engine, "forty.db", age_days=40)
    retention.purge_expired_uploads(now=NOW, factory=app_factory)
    assert file.exists()  # the default is a year
    client.put("/api/v1/admin/retention", json={"retention_days": 30})
    try:
        retention.purge_expired_uploads(now=NOW, factory=app_factory)
        assert not file.exists()
    finally:
        client.put("/api/v1/admin/retention", json={"retention_days": 365})
        with admin_engine.begin() as connection:
            connection.execute(text("DELETE FROM sources WHERE id = :i"), {"i": uuid.UUID(source)})


def test_the_purge_is_scheduled_nightly() -> None:
    from celery.schedules import crontab

    from app.jobs.celery_app import celery_app

    assert celery_app.conf.beat_schedule["purge-uploads"] == {"task": "legacylift.purge_uploads", "schedule": crontab(hour=2, minute=15)}
    assert "legacylift.purge_uploads" in celery_app.tasks


def test_make_job_still_works_for_the_job_lists(analyst) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    listed = [j for j in analyst.get("/api/v1/jobs?limit=200").json()["items"] if j["id"] == job][0]
    assert listed["source_name"] == "shop.db" and listed["owner_name"] and listed["next_stage"] == "discover"
