"""Jobs and background stages: lifecycle, idempotency, retries, failure, locking, cancel, events and the SSE stream.

The stages run eagerly here (no broker). Their work is replaced by small handlers so these tests do not need the REA
runner; the full pipeline on the sample systems is in test_jobs_flow.py.
"""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.jobs import stages as stages_module
from app.jobs.stages import JobLock, Outcome, Stage, StageBusy, run_stage
from tests.conftest import login_as
from tests.test_mapping_api import seeded  # noqa: F401

SQLITE_BYTES = b"SQLite format 3\x00" + b"\x00" * 200


def make_job(client, schema: str | None = None) -> str:  # type: ignore[no-untyped-def]
    uploaded = client.post("/api/v1/sources/upload", files={"file": ("shop.db", SQLITE_BYTES)})
    assert uploaded.status_code == 201, uploaded.text
    body = {"source_id": uploaded.json()["id"]}
    if schema:
        body["target_schema"] = schema
    response = client.post("/api/v1/jobs", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def ids(client, job: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    me = client.get("/api/v1/auth/me").json()
    return uuid.UUID(job), uuid.UUID(me["org_id"]), uuid.UUID(me["user_id"])


@pytest.fixture
def analyst(client, api_users, seeded):  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    return client


@pytest.fixture
def handlers(monkeypatch):  # type: ignore[no-untyped-def]
    """Replace the work of every stage with a recorder that moves the job one step like the real stage would not need to."""
    calls: list[str] = []

    def fake(stage):  # type: ignore[no-untyped-def]
        def handler(db, job, ctx):  # type: ignore[no-untyped-def]
            calls.append(stage.value)
            return {"fake": stage.value}

        return handler

    monkeypatch.setattr(stages_module, "HANDLERS", {s: fake(s) for s in Stage})
    return calls


def test_create_list_get_and_filter(analyst) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst, "commerce")
    one = analyst.get(f"/api/v1/jobs/{job}")
    assert one.status_code == 200
    assert one.json()["status"] == "CREATED" and one.json()["next_stage"] == "discover" and one.json()["target_schema_id"]
    page = analyst.get("/api/v1/jobs?limit=1").json()
    assert set(page) == {"items", "total", "limit", "offset"} and page["limit"] == 1 and page["total"] >= 1 and len(page["items"]) == 1
    assert all(j["status"] == "CREATED" for j in analyst.get("/api/v1/jobs?status=CREATED").json()["items"])
    assert analyst.get("/api/v1/jobs?status=NOPE").status_code == 422
    assert analyst.get("/api/v1/jobs?limit=0").status_code == 422
    assert analyst.get(f"/api/v1/jobs/{uuid.uuid4()}").status_code == 404


def test_create_validates_its_input(analyst) -> None:  # type: ignore[no-untyped-def]
    assert analyst.post("/api/v1/jobs", json={"source_id": str(uuid.uuid4())}).status_code == 404
    uploaded = analyst.post("/api/v1/sources/upload", files={"file": ("a.db", SQLITE_BYTES)}).json()["id"]
    assert analyst.post("/api/v1/jobs", json={"source_id": uploaded, "target_schema": "nope"}).status_code == 422
    assert analyst.post("/api/v1/jobs", json={"source_id": uploaded, "data_source_id": uploaded}).status_code == 422
    assert analyst.post("/api/v1/jobs", json={"source_id": uploaded, "surprise": 1}).status_code == 201  # unknown keys are ignored, not stored


def test_a_stage_runs_in_the_background_and_leaves_events(analyst, handlers) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    response = analyst.post(f"/api/v1/jobs/{job}/stages/discover")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["stages"] == ["discover"] and body["execution"] == "eager" and body["task_id"]
    assert handlers == ["discover"]
    messages = [e["message"] for e in analyst.get(f"/api/v1/jobs/{job}/events").json()["items"]]
    assert "Stage discover started" in messages and any(m and m.startswith("Stage discover finished") for m in messages)


def test_run_pipeline_names_its_stages_and_validates_them(analyst, handlers) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    assert analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": ["profile", "profile"]}).status_code == 422
    assert analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": []}).status_code == 422
    assert analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": ["explode"]}).status_code == 422
    assert analyst.post(f"/api/v1/jobs/{job}/stages/explode").status_code == 422
    # mapping needs a target schema, from the job or from the request
    assert analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": ["map"]}).status_code == 422
    assert analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": ["map"], "target_schema": "nope"}).status_code == 422
    # stages are run in canonical order whatever the request says
    queued = analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": ["rules", "discover"]})
    assert queued.status_code == 202 and queued.json()["stages"] == ["discover", "rules"]
    assert handlers == ["discover", "rules"]


def test_a_stage_that_is_already_behind_the_job_is_skipped(analyst, handlers) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    job_id, org, user = ids(analyst, job)
    first = run_stage(Stage.DISCOVER, job_id, org, user)
    assert first.outcome == Outcome.DONE
    # the fake handler does not move the job, so push it forward the way the real stage would
    from app.db.session import scoped_session

    with scoped_session(org_id=org, user_id=user) as db:
        db.execute(text("UPDATE jobs SET status = 'PROFILED' WHERE id = :j"), {"j": job_id})
    for stage in (Stage.DISCOVER, Stage.PROFILE):
        result = run_stage(stage, job_id, org, user)
        assert result.outcome == Outcome.SKIPPED and result.message.startswith("Already")
    assert handlers == ["discover"]  # nothing ran twice


def test_a_failing_stage_fails_the_job_once_and_later_stages_skip(analyst, handlers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def boom(db, job, ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("secret detail that must not leak: /srv/app/internal.py")

    monkeypatch.setitem(stages_module.HANDLERS, Stage.PROFILE, boom)
    job = make_job(analyst)
    # profile moves nothing in the fake world; discovery first so profile has a legal CREATED -> FAILED move
    result = analyst.post(f"/api/v1/jobs/{job}/stages/profile")
    assert result.status_code == 202
    state = analyst.get(f"/api/v1/jobs/{job}").json()
    assert state["status"] == "FAILED" and "RuntimeError" in state["error_message"] and "secret" not in state["error_message"]
    events = analyst.get(f"/api/v1/jobs/{job}/events").json()["items"]
    assert any(e["to_status"] == "FAILED" and e["detail"].get("stage") == "profile" for e in events)
    assert all("secret" not in json.dumps(e) for e in events)
    assert analyst.post(f"/api/v1/jobs/{job}/stages/map").status_code == 409  # a finished job takes no more stages


def test_transient_errors_are_retried_then_give_up(analyst, handlers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    monkeypatch.setattr(settings, "job_task_max_retries", 3)
    attempts = {"n": 0}

    def flaky(db, job, ctx):  # type: ignore[no-untyped-def]
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        return {"attempt": attempts["n"]}

    monkeypatch.setitem(stages_module.HANDLERS, Stage.DISCOVER, flaky)
    job = make_job(analyst)
    assert analyst.post(f"/api/v1/jobs/{job}/stages/discover").status_code == 202
    assert attempts["n"] == 3  # two failures retried, the third worked
    assert analyst.get(f"/api/v1/jobs/{job}").json()["status"] == "CREATED"  # a transient failure never fails the job

    def always(db, job, ctx):  # type: ignore[no-untyped-def]
        attempts["n"] += 1
        raise OperationalError("SELECT 1", {}, Exception("database restarting"))

    monkeypatch.setitem(stages_module.HANDLERS, Stage.DISCOVER, always)
    attempts["n"] = 0
    other = make_job(analyst)
    assert analyst.post(f"/api/v1/jobs/{other}/stages/discover").status_code == 202
    assert attempts["n"] == settings.job_task_max_retries + 1
    failed = analyst.get(f"/api/v1/jobs/{other}").json()
    assert failed["status"] == "FAILED" and "retries" in failed["error_message"]


def test_one_stage_per_job_at_a_time(analyst, handlers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    job_id, org, user = ids(analyst, job)
    holder = JobLock(job_id)
    assert holder.acquire()
    try:
        with pytest.raises(StageBusy):
            run_stage(Stage.DISCOVER, job_id, org, user)
        # through the task, a stage that stays blocked gives up politely instead of failing the job
        monkeypatch.setattr(get_settings(), "job_stage_busy_retries", 2)
        response = analyst.post(f"/api/v1/jobs/{job}/stages/discover")
        assert response.status_code == 202 and handlers == []
        assert analyst.get(f"/api/v1/jobs/{job}").json()["status"] == "CREATED"
    finally:
        holder.release()
    assert run_stage(Stage.DISCOVER, job_id, org, user).outcome == Outcome.DONE  # the lock is gone, nothing leaked


def test_cancel(analyst, handlers, admin_engine) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    cancelled = analyst.post(f"/api/v1/jobs/{job}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "FAILED" and cancelled.json()["error_message"] == "Cancelled by a user"
    events = analyst.get(f"/api/v1/jobs/{job}/events").json()["items"]
    assert events[-1]["detail"]["cancelled"] is True
    assert analyst.post(f"/api/v1/jobs/{job}/cancel").status_code == 409
    assert analyst.post(f"/api/v1/jobs/{job}/stages/discover").status_code == 409
    assert handlers == []
    assert analyst.post(f"/api/v1/jobs/{uuid.uuid4()}/cancel").status_code == 404

    # a stage that holds the job row (it is changing the state right now) is not interrupted: the answer is 409
    busy = make_job(analyst)
    with admin_engine.connect() as connection, connection.begin():
        connection.execute(text("SELECT id FROM jobs WHERE id = :j FOR UPDATE"), {"j": busy})
        response = analyst.post(f"/api/v1/jobs/{busy}/cancel")
        assert response.status_code == 409 and "stage is running" in response.json()["detail"]
    assert analyst.post(f"/api/v1/jobs/{busy}/cancel").status_code == 200


def test_queue_down_is_a_503_and_the_job_survives(analyst, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.jobs.celery_app import celery_app

    monkeypatch.setattr(get_settings(), "jobs_execution", "celery")
    monkeypatch.setitem(celery_app.conf, "broker_url", "redis://127.0.0.1:1/0")
    job = make_job(analyst)
    response = analyst.post(f"/api/v1/jobs/{job}/stages/discover")
    assert response.status_code == 503 and response.json()["error"]["code"] == "unavailable"
    assert analyst.get(f"/api/v1/jobs/{job}").json()["status"] == "CREATED"


def test_events_list_paginates_by_cursor(analyst, handlers) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    analyst.post(f"/api/v1/jobs/{job}/run", json={"stages": ["discover", "profile"]})
    everything = analyst.get(f"/api/v1/jobs/{job}/events").json()
    assert len(everything["items"]) == 4 and everything["last_id"] == everything["items"][-1]["id"]
    first = analyst.get(f"/api/v1/jobs/{job}/events?limit=1").json()
    rest = analyst.get(f"/api/v1/jobs/{job}/events?after={first['last_id']}").json()
    assert len(rest["items"]) == 3 and rest["items"][0]["id"] > first["last_id"]
    assert analyst.get(f"/api/v1/jobs/{job}/events?after={everything['last_id']}").json() == {"items": [], "last_id": None}


def sse(client, url: str, **kwargs):  # type: ignore[no-untyped-def]
    events, current = [], {}
    with client.stream("GET", url, **kwargs) as response:
        assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line == "":
                if current:
                    events.append(current)
                current = {}
            elif line.startswith(":"):
                events.append({"comment": line})
            else:
                key, _, value = line.partition(": ")
                current[key] = value
    return events


def test_sse_replays_history_and_ends_when_the_job_is_finished(analyst, handlers) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    analyst.post(f"/api/v1/jobs/{job}/stages/discover")
    analyst.post(f"/api/v1/jobs/{job}/cancel")
    events = sse(analyst, f"/api/v1/jobs/{job}/events/stream")
    jobs = [e for e in events if e.get("event") == "job"]
    assert len(jobs) == 3 and [json.loads(e["data"])["to_status"] for e in jobs][-1] == "FAILED"
    assert events[-1]["event"] == "end" and json.loads(events[-1]["data"])["status"] == "FAILED"
    # a browser that reconnects with Last-Event-ID only gets what it missed
    again = sse(analyst, f"/api/v1/jobs/{job}/events/stream", headers={"Last-Event-ID": jobs[1]["id"]})
    assert [e["id"] for e in again if e.get("event") == "job"] == [jobs[2]["id"]]
    resumed = sse(analyst, f"/api/v1/jobs/{job}/events/stream?after={jobs[2]['id']}")
    assert [e for e in resumed if e.get("event") == "job"] == [] and resumed[-1]["event"] == "end"


def test_sse_of_a_running_job_times_out_and_can_resume(analyst, handlers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = get_settings()
    monkeypatch.setattr(settings, "sse_max_seconds", 1)
    monkeypatch.setattr(settings, "sse_poll_seconds", 0.1)
    monkeypatch.setattr(settings, "sse_heartbeat_seconds", 0.2)
    job = make_job(analyst)
    analyst.post(f"/api/v1/jobs/{job}/stages/discover")
    events = sse(analyst, f"/api/v1/jobs/{job}/events/stream")
    assert events[-1]["event"] == "timeout" and json.loads(events[-1]["data"])["status"] == "CREATED"
    assert any(e.get("comment") == ": keepalive" for e in events)  # proof of life while nothing happens


def test_sse_and_events_are_private_to_the_organization(client, analyst, handlers) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    client.cookies.clear()
    assert login_as(client, "admin@globex.test").status_code == 200
    for path in ("", "/events", "/events/stream"):
        assert client.get(f"/api/v1/jobs/{job}{path}").status_code == 404, path
    assert client.post(f"/api/v1/jobs/{job}/cancel").status_code == 404
    assert client.post(f"/api/v1/jobs/{job}/stages/discover").status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/v1/jobs/{job}/events/stream").status_code == 401


def test_a_viewer_can_look_but_not_change(client, analyst) -> None:  # type: ignore[no-untyped-def]
    job = make_job(analyst)
    client.cookies.clear()
    assert login_as(client, "viewer@acme.test").status_code == 200
    assert client.get(f"/api/v1/jobs/{job}").status_code == 200
    assert client.get("/api/v1/jobs").status_code == 200
    assert client.post("/api/v1/jobs", json={"source_id": str(uuid.uuid4())}).status_code == 403
    for path in ("run", "stages/discover", "cancel"):
        assert client.post(f"/api/v1/jobs/{job}/{path}").status_code == 403, path


def test_the_celery_application_is_wired_for_safe_redelivery() -> None:
    from app.jobs.celery_app import celery_app
    from app.jobs.tasks import TASK_NAME

    assert TASK_NAME in celery_app.tasks
    conf = celery_app.conf
    assert conf.task_acks_late and conf.task_reject_on_worker_lost and conf.worker_prefetch_multiplier == 1
    assert conf.task_serializer == "json" and list(conf.accept_content) == ["json"]  # no pickle from the broker
    assert conf.task_default_queue == "legacylift" and conf.task_time_limit > conf.task_soft_time_limit
    assert celery_app.conf.broker_url.startswith("redis://")


def test_eager_execution_is_refused_outside_local_use(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import Settings

    with pytest.raises(ValueError, match="eager"):
        Settings(environment="production", jobs_execution="eager", jwt_secret="x" * 40, upload_encryption_key="y" * 44)
    with pytest.raises(ValueError, match="celery or eager"):
        Settings(jobs_execution="threads")
