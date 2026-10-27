"""The operations detail, the cohort retention and the operational side of the simulated history."""

import random
from collections import Counter
from datetime import date

import pytest
from sqlalchemy import text

from app.billing import history_ops
from app.billing.analytics import METHODS, STAGES
from app.billing.history import generate_history
from tests.conftest import HISTORY_END, login_as


def sql(engine, query: str, **params):  # type: ignore[no-untyped-def]
    with engine.connect() as c:
        return c.execute(text(query), params).scalar()


@pytest.fixture
def analyst(client, history, api_users):  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    return client


@pytest.fixture
def admin(client, history, api_users):  # type: ignore[no-untyped-def]
    assert login_as(client, "admin@acme.test").status_code == 200
    return client


WINDOW = {"from": "2026-06-01", "to": "2026-09-30"}


def test_the_operations_detail_agrees_with_the_database(analyst, admin_engine) -> None:  # type: ignore[no-untyped-def]
    body = analyst.get("/api/v1/analytics/operations/detail", params=WINDOW).json()
    assert body["range"] == WINDOW
    in_window = "(created_at AT TIME ZONE 'UTC')::date BETWEEN '2026-06-01' AND '2026-09-30'"
    assert body["mapping_total"] == sum(body["mapping_methods"].values()) == sql(admin_engine, f"SELECT count(*) FROM mappings WHERE {in_window}")
    assert set(body["mapping_methods"]) == set(METHODS)
    for method in METHODS:
        assert body["mapping_methods"][method] == sql(admin_engine, f"SELECT count(*) FROM mappings WHERE method = :m AND {in_window}", m=method)
    assert body["mapping_methods"]["memory"] > body["mapping_methods"]["llm"] > 0  # the memory does most of the work, the model the least
    assert sum(body["quarantine_reasons"].values()) == sql(admin_engine, f"SELECT count(*) FROM quarantined_rows WHERE {in_window}")
    assert sum(body["discovery_confidence"]) == sql(admin_engine, f"SELECT count(*) FROM source_columns WHERE discovery_confidence IS NOT NULL AND {in_window}")
    assert len(body["discovery_confidence"]) == 10 and body["discovery_confidence"][-1] > body["discovery_confidence"][0]  # most columns are certain
    assert sum(body["failed_stages"].values()) == sql(admin_engine, f"SELECT count(*) FROM job_events WHERE to_status = 'FAILED' AND detail ? 'stage' AND {in_window}")


def test_stage_latency_is_measured_per_stage_with_percentiles(analyst, admin_engine) -> None:  # type: ignore[no-untyped-def]
    latency = analyst.get("/api/v1/analytics/operations/detail", params=WINDOW).json()["stage_latency"]
    assert set(latency) == set(STAGES) and list(latency) == STAGES  # in pipeline order
    for stage, figures in latency.items():
        assert 0 < figures["p50"] <= figures["p95"] and figures["count"] > 100, stage
    assert latency["load"]["p95"] > 5 * latency["rules"]["p95"]  # loading takes far longer than generating rules
    checked = sql(admin_engine, "SELECT count(*) FROM job_events WHERE message LIKE 'Stage load finished%' AND (created_at AT TIME ZONE 'UTC')::date BETWEEN '2026-06-01' AND '2026-10-01'")
    assert latency["load"]["count"] <= checked


def test_the_model_figures_and_token_savings(analyst, admin_engine) -> None:  # type: ignore[no-untyped-def]
    body = analyst.get("/api/v1/analytics/operations/detail", params=WINDOW).json()
    llm = body["llm"]
    in_window = "(created_at AT TIME ZONE 'UTC')::date BETWEEN '2026-06-01' AND '2026-09-30'"
    assert llm["cache_hits"] == sql(admin_engine, f"SELECT count(*) FROM llm_calls WHERE cache_hit AND {in_window}")
    assert llm["calls_made"] == sql(admin_engine, f"SELECT count(*) FROM llm_calls WHERE status = 'ok' AND NOT cache_hit AND {in_window}")
    assert llm["cache_hit_rate"] == pytest.approx(llm["cache_hits"] / (llm["cache_hits"] + llm["calls_made"]), abs=1e-4) and 0 < llm["cache_hit_rate"] < 1
    methods = body["mapping_methods"]
    assert body["tokens_saved_by_memory_estimate"] == round(methods["memory"] * llm["tokens"] / methods["llm"])
    assert "estimate" in body["footnote"] and body["tokens_saved_by_cache"] >= 0


def test_the_detail_is_for_analysts_and_admins_and_validates_its_range(client, analyst) -> None:  # type: ignore[no-untyped-def]
    assert analyst.get("/api/v1/analytics/operations/detail", params={"from": "2026-05-02", "to": "2026-05-01"}).status_code == 422
    assert analyst.get("/api/v1/analytics/operations/detail", params={"from": "2022-01-01", "to": "2026-01-01"}).status_code == 422
    quiet = analyst.get("/api/v1/analytics/operations/detail", params={"from": "2010-01-01", "to": "2010-01-31"})
    assert quiet.status_code == 200 and quiet.json()["mapping_total"] == 0 and quiet.json()["stage_latency"] == {} and quiet.json()["llm"]["cache_hit_rate"] is None
    default = analyst.get("/api/v1/analytics/operations/detail").json()["range"]
    assert default["to"] == HISTORY_END.isoformat()
    client.cookies.clear()
    assert login_as(client, "viewer@acme.test").status_code == 200
    assert client.get("/api/v1/analytics/operations/detail").status_code == 403


def test_cohort_retention_follows_each_signup_month(admin, admin_engine) -> None:  # type: ignore[no-untyped-def]
    body = admin.get("/api/v1/analytics/revenue/cohorts").json()
    assert body["as_of"] == HISTORY_END.isoformat() and "simulated" in body["footnote"]
    cohorts = body["cohorts"]
    assert len(cohorts) >= 20 and [c["month"] for c in cohorts] == sorted(c["month"] for c in cohorts)
    assert sum(c["size"] for c in cohorts) == sql(admin_engine, "SELECT count(DISTINCT org_id) FROM subscriptions")
    for cohort in cohorts:
        assert cohort["logos"][0] == 100.0 and all(0 <= v <= 100 for v in cohort["logos"])  # everyone is there in the month they join
        assert len(cohort["logos"]) == len(cohort["mrr"])
    first = cohorts[0]
    assert len(first["logos"]) > len(cohorts[-1]["logos"])  # older cohorts have more months behind them
    assert min(first["logos"]) < 100  # and some customers have left


def test_a_cohort_cell_matches_a_recomputation_from_the_subscriptions(admin, admin_engine) -> None:  # type: ignore[no-untyped-def]
    cohorts = admin.get("/api/v1/analytics/revenue/cohorts").json()["cohorts"]
    cohort = next(c for c in cohorts if c["month"] == "2025-11")
    k = 6
    month_end = date(2026, 5, 31)  # the cohort's month plus six: November 2025 + 6 = May 2026
    orgs = sql(admin_engine, "SELECT count(*) FROM (SELECT org_id FROM subscriptions GROUP BY org_id HAVING to_char(date_trunc('month', min((started_at AT TIME ZONE 'UTC')::date)), 'YYYY-MM') = '2025-11') x")
    active = sql(
        admin_engine,
        """SELECT count(*) FROM (SELECT org_id FROM subscriptions GROUP BY org_id HAVING to_char(date_trunc('month', min((started_at AT TIME ZONE 'UTC')::date)), 'YYYY-MM') = '2025-11') c
           WHERE EXISTS (SELECT 1 FROM subscriptions s WHERE s.org_id = c.org_id AND (s.started_at AT TIME ZONE 'UTC')::date <= :md AND (s.ended_at IS NULL OR (s.ended_at AT TIME ZONE 'UTC')::date > :md))""",
        md=month_end,
    )
    assert cohort["size"] == orgs and cohort["logos"][k] == round(100.0 * active / orgs, 1)


def test_cohorts_are_admin_only(client, history, api_users) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    assert client.get("/api/v1/analytics/revenue/cohorts").status_code == 403


# ------------------------------------------------------------------ the operational side of the simulated history


@pytest.fixture(scope="module")
def data():  # type: ignore[no-untyped-def]
    return generate_history(42, HISTORY_END)


@pytest.fixture(scope="module")
def ops(data):  # type: ignore[no-untyped-def]
    return history_ops.operational_rows(data, {i: o.periods[-1].plan for i, o in enumerate(data.orgs)}, random.Random(44))


def test_every_job_has_a_timeline_that_ends_where_the_job_ends(data, ops) -> None:  # type: ignore[no-untyped-def]
    by_job: dict[object, list[dict]] = {}
    for event in ops["job_events"]:
        by_job.setdefault(event["job_id"], []).append(event)
    assert set(by_job) == {j.id for j in data.jobs}
    for job in data.jobs[:600]:
        events = by_job[job.id]
        times = [e["created_at"] for e in events]
        assert times == sorted(times), "events are in order"
        assert events[0]["from_status"] == "CREATED" and events[0]["to_status"] == "UPLOADED"
        last = events[-1]
        assert last["to_status"] == ("COMPLETED" if job.ok else "FAILED")
        if job.ok:
            assert abs((last["created_at"] - job.completed).total_seconds()) <= 2 and last["detail"] == {"loaded": job.records}
        else:
            assert last["detail"]["stage"] in {"discover", "profile", "map", "load"} and "failed" in last["message"]


def test_stage_notes_come_in_pairs_in_the_words_the_workers_use(ops) -> None:  # type: ignore[no-untyped-def]
    started, finished = Counter(), Counter()
    for e in ops["job_events"]:
        message = e["message"] or ""
        if message.startswith("Stage ") and message.endswith(" started"):
            started[(e["job_id"], message.split()[1])] += 1
        if message.startswith("Stage ") and " finished" in message:
            finished[(e["job_id"], message.split()[1])] += 1
    assert started and set(finished) <= set(started) and all(v == 1 for v in started.values())
    assert len(started) - len(finished) < len({k[0] for k in started}) * 0.1 + 1  # only the stage a job failed in has no "finished" note


def test_the_memory_does_more_of_the_mapping_as_an_organization_repeats(data, ops) -> None:  # type: ignore[no-untyped-def]
    jobs = {j.id: j for j in data.jobs}
    ordinal: Counter = Counter()
    seen: dict[int, int] = {}
    for j in data.jobs:
        if j.ok:
            seen[j.org] = seen.get(j.org, -1) + 1
            ordinal[j.id] = seen[j.org]
    share = {"first": [0, 0], "later": [0, 0]}
    for m in ops["mappings"]:
        bucket = "first" if ordinal[m["job_id"]] == 0 else "later"
        share[bucket][0] += m["method"] == "memory"
        share[bucket][1] += 1
    assert share["first"][0] == 0  # nothing to remember on a first system
    assert share["later"][0] / share["later"][1] > 0.4 and len(jobs) > 0
    methods = Counter(m["method"] for m in ops["mappings"])
    assert set(methods) == set(METHODS) and methods["llm"] < methods["embedding"] < methods["memory"]


def test_a_database_is_certain_and_a_recovered_binary_layout_less_so(data, ops) -> None:  # type: ignore[no-untyped-def]
    org_of = {j.id: j.org for j in data.jobs}
    sqlite = [c["discovery_confidence"] for c in ops["source_columns"] if org_of[c["job_id"]] % 3 != 0]
    binary = [c["discovery_confidence"] for c in ops["source_columns"] if org_of[c["job_id"]] % 3 == 0]
    assert set(sqlite) == {1.0} and min(binary) >= 0.45 and 0.7 < sum(binary) / len(binary) < 0.95 and max(binary) <= 1.0


def test_the_operational_rows_are_deterministic(data) -> None:  # type: ignore[no-untyped-def]
    plans = {i: o.periods[-1].plan for i, o in enumerate(data.orgs)}
    a = history_ops.operational_rows(data, plans, random.Random(44))
    b = history_ops.operational_rows(data, plans, random.Random(44))
    assert [(e["job_id"], e["message"], e["created_at"]) for e in a["job_events"][:500]] == [(e["job_id"], e["message"], e["created_at"]) for e in b["job_events"][:500]]
    assert [m["method"] for m in a["mappings"][:500]] == [m["method"] for m in b["mappings"][:500]] and len(a["quarantined_rows"]) == len(b["quarantined_rows"])


def test_a_range_can_be_given_as_a_number_of_days_ending_at_the_latest_data(admin) -> None:  # type: ignore[no-untyped-def]
    latest = admin.get("/api/v1/analytics/revenue").json()["range"]["to"]
    thirty = admin.get("/api/v1/analytics/revenue", params={"days": 30}).json()
    assert thirty["range"]["to"] == latest
    assert (date.fromisoformat(thirty["range"]["to"]) - date.fromisoformat(thirty["range"]["from"])).days == 29
    assert thirty["kpis"]["days"] == 30
    explicit = admin.get("/api/v1/analytics/revenue", params={"days": 30, "from": "2026-09-01"}).json()
    assert explicit["range"]["from"] == "2026-09-01"  # an explicit start wins
    assert admin.get("/api/v1/analytics/revenue", params={"days": 0}).status_code == 422
    assert admin.get("/api/v1/analytics/revenue", params={"days": 5000}).status_code == 422


def test_the_csv_exports_honour_the_number_of_days(admin) -> None:  # type: ignore[no-untyped-def]
    for kind in ("revenue", "operations"):
        response = admin.get(f"/api/v1/analytics/{kind}.csv", params={"days": 7})
        assert response.status_code == 200
        assert len(response.text.strip().splitlines()) == 8  # the header and seven days
