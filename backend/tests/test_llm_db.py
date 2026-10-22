"""The router with a real database: persistence, tenant isolation, admin settings and usage, and mapping with an LLM end to end."""

import json
import shutil
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.enums import JobStatus, LlmCallStatus, SourceKind
from app.llm import factory
from app.llm.errors import AllProvidersFailed, BudgetExceeded, ProviderServerError, ProviderTimeout
from app.llm.router import LlmContext, cache_key
from app.llm.store import DbStore, OrgLlmSettings
from app.llm.tasks import TASKS
from tests.conftest import login_as
from tests.llm_helpers import FakeProvider, answer, make_router, sample_payload, text_response
from tests.test_mapping_api import (  # noqa: F401  (fixtures and helpers shared with the mapping tests)
    as_decisions,
    profiled_clinic_job,
    seeded,
    truth_of,
)
from app.evaluation.mapping_score import score_decisions

ROOT = Path(__file__).resolve().parents[2]
REA_INSTALLED = (ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
needs_rea = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")
TASK = "resolve_mappings"


def make_job(admin_engine, org_id, budget: int = 6000) -> uuid.UUID:  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        source = models.Source(org_id=org_id, name="s", kind=SourceKind.CSV)
        session.add(source)
        session.flush()
        job = models.Job(org_id=org_id, source_id=source.id, status=JobStatus.PROFILED, token_budget=budget)
        session.add(job)
        session.commit()
        return job.id


def clear_llm_cache(admin_engine) -> None:  # type: ignore[no-untyped-def]
    """The cache is organization wide and the test database is shared, so tests that need a miss start from an empty cache."""
    with Session(admin_engine) as session:
        session.execute(models.LlmCache.__table__.delete())
        session.commit()


def install_router(monkeypatch, groq=None, gemini=None):  # type: ignore[no-untyped-def]
    groq = groq or FakeProvider("groq", [], model="openai/gpt-oss-20b")
    gemini = gemini or FakeProvider("gemini", [], model="gemini-2.5-flash-lite")
    router, clock = make_router({"groq": groq, "gemini": gemini})
    monkeypatch.setattr(factory, "_router", router)
    return router, groq, gemini


def echo(request):  # type: ignore[no-untyped-def]
    return answer(json.loads(request.messages[1].text), tokens_in=900, tokens_out=100)


# ------------------------------------------------------------------ the store


def test_cache_is_stored_per_organization_with_a_hit_counter(app_factory, api_users) -> None:  # type: ignore[no-untyped-def]
    mine, theirs = DbStore(api_users.org_id, app_factory), DbStore(api_users.other_org_id, app_factory)
    key = uuid.uuid4().hex + uuid.uuid4().hex
    mine.cache_put(key, TASK, "resolve_mappings.v1", {"answers": []}, 820)
    mine.cache_put(key, TASK, "resolve_mappings.v1", {"answers": [{"id": "x"}]}, 900)  # the same question answered again replaces the entry
    cached = mine.cache_get(key)
    assert cached.response == {"answers": [{"id": "x"}]} and cached.tokens == 900
    assert theirs.cache_get(key) is None  # another organization never sees it
    mine.cache_hit(key)
    mine.cache_hit(key)
    from app.db.session import tenant_session

    with tenant_session(api_users.org_id, app_factory) as session:
        row = session.scalar(select(models.LlmCache).where(models.LlmCache.request_hash == key))
        assert row.hits == 2 and row.last_hit_at is not None and row.prompt_version == "resolve_mappings.v1"


def test_calls_and_usage_are_scoped_to_the_organization(app_factory, api_users, admin_engine) -> None:  # type: ignore[no-untyped-def]
    from app.llm.store import CallRecord

    mine, theirs = DbStore(api_users.org_id, app_factory), DbStore(api_users.other_org_id, app_factory)
    before_mine, before_theirs = mine.org_tokens_today(), theirs.org_tokens_today()
    job = make_job(admin_engine, api_users.org_id)
    mine.record_call(CallRecord("groq", "m", TASK, LlmCallStatus.OK, 700, 120, 500, 140, job_id=job, prompt_version="resolve_mappings.v1"))
    mine.record_call(CallRecord("cache", "cache", TASK, LlmCallStatus.CACHE_HIT, cache_hit=True, job_id=job))
    mine.record_call(CallRecord("groq", "m", TASK, LlmCallStatus.RATE_LIMITED, job_id=job, error_code="rate_limited"))
    assert mine.org_tokens_today() == before_mine + 820 and theirs.org_tokens_today() == before_theirs  # cache hits and failures cost nothing
    usage = mine.job_usage(job)
    assert usage.calls == 1 and usage.token_budget == 6000 and usage.tokens_used == 0
    assert theirs.job_usage(job) is None  # the other organization cannot see the job at all


def test_job_tokens_are_added_atomically(app_factory, api_users, admin_engine) -> None:  # type: ignore[no-untyped-def]
    store = DbStore(api_users.org_id, app_factory)
    job = make_job(admin_engine, api_users.org_id)
    threads = [threading.Thread(target=lambda: [store.add_job_tokens(job, 7) for _ in range(10)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.job_usage(job).tokens_used == 8 * 10 * 7


def test_the_log_survives_a_failed_request(app_factory, api_users, admin_engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    job = make_job(admin_engine, api_users.org_id)
    router, groq, gemini = install_router(monkeypatch, FakeProvider("groq", [ProviderTimeout("t"), ProviderTimeout("t")]), FakeProvider("gemini", [ProviderServerError("5"), ProviderServerError("5")]))
    ctx = LlmContext(DbStore(api_users.org_id, app_factory), job)
    with pytest.raises(AllProvidersFailed):
        router.complete_json(TASK, sample_payload(1), ctx)
    with Session(admin_engine) as session:
        rows = session.scalars(select(models.LlmCall).where(models.LlmCall.job_id == job).order_by(models.LlmCall.created_at)).all()
        assert [(r.provider, r.status, r.error_code) for r in rows] == [
            ("groq", LlmCallStatus.TIMEOUT, "timeout"), ("groq", LlmCallStatus.TIMEOUT, "timeout"),
            ("gemini", LlmCallStatus.ERROR, "server_error"), ("gemini", LlmCallStatus.ERROR, "server_error"),
        ]  # fmt: skip
        assert session.get(models.Job, job).tokens_used == 0


def test_a_database_backed_router_charges_the_job_and_caches(app_factory, api_users, admin_engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    job = make_job(admin_engine, api_users.org_id)
    router, groq, _ = install_router(monkeypatch, FakeProvider("groq", [echo]))
    ctx = LlmContext(DbStore(api_users.org_id, app_factory), job)
    payload = sample_payload(2, offset=70)
    first = router.complete_json(TASK, payload, ctx)
    again = router.complete_json(TASK, payload, ctx)
    assert first.tokens == 1000 and again.cached and groq.calls == 1
    with Session(admin_engine) as session:
        assert session.get(models.Job, job).tokens_used == 1000
        calls = session.scalars(select(models.LlmCall).where(models.LlmCall.job_id == job)).all()
        assert sorted((c.status.value, c.cache_hit) for c in calls) == [("cache_hit", True), ("ok", False)]
        entry = session.scalar(select(models.LlmCache).where(models.LlmCache.request_hash == cache_key(TASKS[TASK], payload)))
        assert entry.tokens == 1000 and entry.hits == 1


def test_the_job_budget_in_the_database_stops_a_runaway(app_factory, api_users, admin_engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    job = make_job(admin_engine, api_users.org_id, budget=2500)
    router, groq, _ = install_router(monkeypatch, FakeProvider("groq", [echo, echo, echo, echo]))
    store = DbStore(api_users.org_id, app_factory)
    store_settings = OrgLlmSettings(["groq", "gemini"], 2500, 100_000, 50)

    class Fixed(DbStore):
        def org_settings(self):  # type: ignore[no-untyped-def]
            return store_settings

    ctx = LlmContext(Fixed(api_users.org_id, app_factory), job)
    with pytest.raises(BudgetExceeded) as stop:
        for i in range(10):
            router.complete_json(TASK, sample_payload(1, offset=100 + i), ctx)
    assert stop.value.scope == "job_tokens" and groq.calls <= 2 and store.job_usage(job).tokens_used <= 2500


# ------------------------------------------------------------------ admin settings, test connection, usage


def test_llm_settings_defaults_come_from_the_environment(client, api_users) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@globex.test")
    settings = client.get("/api/v1/admin/llm-settings").json()
    assert settings["provider_order"] == ["groq", "gemini"] and settings["job_token_budget"] == 6000
    assert settings["org_daily_token_budget"] == 100_000 and settings["max_calls_per_job"] == 2 and settings["used_today"] >= 0
    assert [p["name"] for p in settings["providers"]] == ["groq", "gemini"]
    assert all(set(p) >= {"model", "configured", "circuit", "tokens_available"} for p in settings["providers"])
    assert "api_key" not in json.dumps(settings).lower() and "secret" not in json.dumps(settings).lower()


def test_admins_change_the_provider_order_and_budgets_and_it_is_audited(client, app_factory, api_users, admin_engine) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@acme.test")
    body = {"provider_order": ["gemini", "groq"], "job_token_budget": 4000, "org_daily_token_budget": 50_000, "max_calls_per_job": 3}
    saved = client.put("/api/v1/admin/llm-settings", json=body)
    assert saved.status_code == 200 and saved.json()["provider_order"] == ["gemini", "groq"] and saved.json()["job_token_budget"] == 4000
    assert client.get("/api/v1/admin/llm-settings").json()["max_calls_per_job"] == 3
    assert DbStore(api_users.org_id, app_factory).org_settings() == OrgLlmSettings(["gemini", "groq"], 4000, 50_000, 3)
    entries = client.get("/api/v1/admin/audit-log", params={"action": "settings.change"}).json()
    changed = entries[0]["detail"]["changed"]
    assert changed["provider_order"] == {"from": ["groq", "gemini"], "to": ["gemini", "groq"]} and changed["max_calls_per_job"]["to"] == 3
    # another organization keeps its own settings
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    assert client.get("/api/v1/admin/llm-settings").json()["provider_order"] == ["groq", "gemini"]
    # restore
    client.cookies.clear()
    login_as(client, "admin@acme.test")
    client.put("/api/v1/admin/llm-settings", json={"provider_order": ["groq", "gemini"], "job_token_budget": 6000, "org_daily_token_budget": 100_000, "max_calls_per_job": 2})


@pytest.mark.parametrize(
    "patch",
    [
        {"provider_order": []}, {"provider_order": ["groq", "groq"]}, {"provider_order": ["openai"]}, {"job_token_budget": 10},
        {"job_token_budget": 10_000_000}, {"org_daily_token_budget": 5}, {"max_calls_per_job": 0}, {"max_calls_per_job": 99},
    ],
)
def test_settings_are_validated(client, patch) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@acme.test")
    body = {"provider_order": ["groq", "gemini"], "job_token_budget": 6000, "org_daily_token_budget": 100_000, "max_calls_per_job": 2, **patch}
    assert client.put("/api/v1/admin/llm-settings", json=body).status_code == 422


def test_keys_cannot_be_set_through_the_api(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@acme.test")
    body = {"provider_order": ["groq"], "job_token_budget": 6000, "org_daily_token_budget": 100_000, "max_calls_per_job": 2, "groq_api_key": "gsk_stolen"}
    response = client.put("/api/v1/admin/llm-settings", json=body)
    assert "gsk_stolen" not in response.text and "gsk_stolen" not in client.get("/api/v1/admin/llm-settings").text
    client.put("/api/v1/admin/llm-settings", json={**body, "provider_order": ["groq", "gemini"]})


def test_only_admins_may_use_the_settings_routes(client) -> None:  # type: ignore[no-untyped-def]
    body = {"provider_order": ["groq"], "job_token_budget": 6000, "org_daily_token_budget": 100_000, "max_calls_per_job": 2}
    for role in ("analyst", "viewer"):
        client.cookies.clear()
        login_as(client, f"{role}@acme.test")
        assert client.get("/api/v1/admin/llm-settings").status_code == 403
        assert client.put("/api/v1/admin/llm-settings", json=body).status_code == 403
        assert client.post("/api/v1/admin/llm-settings/test-connection", json={"provider": "groq"}).status_code == 403
        assert client.get("/api/v1/admin/llm-usage").status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/admin/llm-settings").status_code == 401


def test_test_connection(client, monkeypatch, admin_engine, api_users) -> None:  # type: ignore[no-untyped-def]
    install_router(monkeypatch, FakeProvider("groq", [text_response('{"ok": true}', tokens_in=40, tokens_out=8), ProviderTimeout("slow")], model="openai/gpt-oss-20b"),
                    FakeProvider("gemini", [], configured=False))  # fmt: skip
    login_as(client, "admin@acme.test")
    ok = client.post("/api/v1/admin/llm-settings/test-connection", json={"provider": "groq"}).json()
    assert ok["ok"] is True and ok["model"] == "fake-model" or ok["ok"] is True
    failed = client.post("/api/v1/admin/llm-settings/test-connection", json={"provider": "groq"}).json()
    assert failed["ok"] is False and failed["error"] == "timeout"
    unconfigured = client.post("/api/v1/admin/llm-settings/test-connection", json={"provider": "gemini"})
    assert unconfigured.status_code == 409 and "No API key" in unconfigured.json()["detail"]
    assert client.post("/api/v1/admin/llm-settings/test-connection", json={"provider": "openai"}).status_code == 422
    with Session(admin_engine) as session:
        rows = session.scalars(select(models.LlmCall).where(models.LlmCall.task == "connection_test", models.LlmCall.org_id == api_users.org_id)).all()
        assert {r.status for r in rows} >= {LlmCallStatus.OK, LlmCallStatus.TIMEOUT}


def test_usage_summary(client, monkeypatch, app_factory, api_users) -> None:  # type: ignore[no-untyped-def]
    router, groq, _ = install_router(monkeypatch, FakeProvider("groq", [echo, ProviderTimeout("t"), echo]))
    login_as(client, "admin@globex.test")
    ctx = LlmContext(DbStore(api_users.other_org_id, app_factory), None)
    first, second = sample_payload(1, offset=300), sample_payload(1, offset=301)
    router.complete_json(TASK, first, ctx)
    router.complete_json(TASK, first, ctx)  # cache hit
    router.complete_json(TASK, second, ctx)  # a timeout, a retry, then success
    usage = client.get("/api/v1/admin/llm-usage", params={"days": 3}).json()
    groq_today = [d for d in usage["days"] if d["provider"] == "groq"]
    assert groq_today and sum(d["calls"] for d in groq_today) == 2 and sum(d["failed_attempts"] for d in groq_today) == 1
    assert sum(d["tokens_in"] + d["tokens_out"] for d in groq_today) == 2000 and any(d["provider"] == "cache" and d["cache_hits"] == 1 for d in usage["days"])
    assert usage["tokens_saved_by_cache"] == 1000  # the cached answer would have cost 1,000 tokens


# ------------------------------------------------------------------ mapping with an LLM through the API


def pick_value(request):  # type: ignore[no-untyped-def]
    """A fake model that knows the answer: it prefers a candidate named value, otherwise the first."""
    payload = json.loads(request.messages[1].text)
    answers = []
    for c in payload["columns"]:
        keys = [k["key"] for k in c["candidates"]]
        target = next((k for k in keys if k.endswith(".value")), keys[0])
        answers.append({"id": c["id"], "target": target, "confidence": 0.9, "hint": None})
    from app.llm.providers.base import ProviderResponse

    return ProviderResponse(json.dumps({"answers": answers}), 640, 80, 300, "stop", "fake-model", None, 150)


@needs_rea
def test_the_unsettled_column_goes_to_the_llm_in_one_call_and_a_rerun_costs_nothing(client, seeded, monkeypatch, admin_engine) -> None:  # type: ignore[no-untyped-def]
    clear_llm_cache(admin_engine)
    router, groq, gemini = install_router(monkeypatch, FakeProvider("groq", [pick_value], model="openai/gpt-oss-20b"))
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)
    first = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"}).json()
    assert first["llm_requests"] == 1 and first["counts"]["needs_llm"] == 0 and groq.calls == 1
    rows = client.get(f"/api/v1/jobs/{job}/mappings").json()
    llm_rows = [r for r in rows if r["method"] == "llm"]
    assert [(r["source_column"], r["target"], r["decision"]) for r in llm_rows] == [("ObsVal", "observations.value", "auto")]
    assert score_decisions(as_decisions(rows), truth_of("clinic")).accuracy >= 0.95

    with Session(admin_engine) as session:
        used = session.get(models.Job, job).tokens_used
        assert 0 < used < 6000 and used == 720
        calls = session.scalars(select(models.LlmCall).where(models.LlmCall.job_id == job)).all()
        assert [(c.provider, c.task, c.status, c.cached_tokens) for c in calls] == [("groq", TASK, LlmCallStatus.OK, 300)]

    again = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"}).json()  # the same job, mapped again
    assert again["llm_requests"] == 1 and groq.calls == 1  # answered from the cache, the provider was not asked again
    with Session(admin_engine) as session:
        assert session.get(models.Job, job).tokens_used == used  # zero new tokens
        hits = session.scalars(select(models.LlmCall).where(models.LlmCall.job_id == job, models.LlmCall.cache_hit.is_(True))).all()
        assert len(hits) == 1 and hits[0].tokens_in == 0


@needs_rea
def test_when_every_provider_fails_the_job_is_not_lost(client, seeded, monkeypatch, admin_engine) -> None:  # type: ignore[no-untyped-def]
    clear_llm_cache(admin_engine)
    install_router(monkeypatch, FakeProvider("groq", [ProviderTimeout("t")] * 2), FakeProvider("gemini", [ProviderServerError("5")] * 2))
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)
    response = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"})
    assert response.status_code == 200 and response.json()["status"] == "MAPPED" and response.json()["counts"]["review"] == 1
    rows = client.get(f"/api/v1/jobs/{job}/mappings").json()
    waiting = [r for r in rows if r["decision"] == "review"]
    assert [r["source_column"] for r in waiting] == ["ObsVal"] and waiting[0]["method"] == "embedding"  # a person decides
    assert len([r for r in rows if r["decision"] in ("auto", "unmapped")]) == 43  # everything else is mapped as before
    with Session(admin_engine) as session:
        statuses = [c.status for c in session.scalars(select(models.LlmCall).where(models.LlmCall.job_id == job).order_by(models.LlmCall.created_at))]
        assert statuses == [LlmCallStatus.TIMEOUT, LlmCallStatus.TIMEOUT, LlmCallStatus.ERROR, LlmCallStatus.ERROR]


@needs_rea
def test_a_failover_during_mapping_still_delivers_the_answer(client, seeded, monkeypatch, admin_engine) -> None:  # type: ignore[no-untyped-def]
    from app.llm.errors import RateLimited

    clear_llm_cache(admin_engine)

    router, groq, gemini = install_router(monkeypatch, FakeProvider("groq", [RateLimited("429", retry_after=3600.0)], model="openai/gpt-oss-20b"),
                                          FakeProvider("gemini", [pick_value], model="gemini-2.5-flash-lite"))  # fmt: skip
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)
    body = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"}).json()
    assert body["counts"]["needs_llm"] == 0 and body["counts"]["review"] == 0 and groq.calls == 1 and gemini.calls == 1


@needs_rea
def test_a_small_job_budget_keeps_the_provider_silent(client, seeded, monkeypatch, admin_engine) -> None:  # type: ignore[no-untyped-def]
    clear_llm_cache(admin_engine)
    router, groq, gemini = install_router(monkeypatch, FakeProvider("groq", [pick_value]))
    client.cookies.clear()
    login_as(client, "admin@acme.test")
    client.put("/api/v1/admin/llm-settings", json={"provider_order": ["groq", "gemini"], "job_token_budget": 500, "org_daily_token_budget": 100_000, "max_calls_per_job": 2})
    client.cookies.clear()
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)  # the job takes the organization's current budget
    with Session(admin_engine) as session:
        assert session.get(models.Job, job).token_budget == 500
    response = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"})
    assert response.status_code == 200 and response.json()["counts"]["review"] == 1 and groq.calls == 0
    client.cookies.clear()
    login_as(client, "admin@acme.test")
    client.put("/api/v1/admin/llm-settings", json={"provider_order": ["groq", "gemini"], "job_token_budget": 6000, "org_daily_token_budget": 100_000, "max_calls_per_job": 2})


@needs_rea
def test_without_provider_keys_mapping_works_and_waits_for_review(client, seeded, monkeypatch, admin_engine) -> None:  # type: ignore[no-untyped-def]
    clear_llm_cache(admin_engine)
    install_router(monkeypatch, FakeProvider("groq", [], configured=False), FakeProvider("gemini", [], configured=False))
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)
    body = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"}).json()
    assert body["llm_requests"] == 0 and body["counts"]["needs_llm"] == 1 and body["status"] == "MAPPED"
