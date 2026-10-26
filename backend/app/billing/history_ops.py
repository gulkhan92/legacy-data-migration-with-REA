"""The operational side of the simulated history: what a migration leaves behind besides usage and invoices.

For every simulated job this writes its event timeline (the state changes and the "stage started / finished" progress notes the real
workers write, so the latency percentiles are computed by the same query as for real jobs), for every completed job the columns it
mapped and how (memory, embedding, model or a person, with the share of memory growing as an organization repeats similar systems),
the discovery confidence of each column (always 1.0 for a database, lower for a recovered binary layout), and a sample of the rows it
quarantined with their reasons. As everything simulated it belongs to simulated organizations.
"""

import math
import random
import uuid
from datetime import timedelta
from typing import Any

from app.billing.history import History, SimJob

STAGE_ORDER = ["discover", "profile", "map", "rules", "dry_run"]
QUARANTINE_REASONS = [("required_null", 0.32), ("invalid_date", 0.22), ("duplicate", 0.16), ("orphan_fk", 0.16), ("out_of_range", 0.14)]
FAIL_STAGES = [("discover", 0.2), ("profile", 0.1), ("map", 0.15), ("load", 0.55)]
TARGET_TABLES = ["customers", "orders", "products", "order_items"]


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    return rng.choices([k for k, _ in weighted], weights=[w for _, w in weighted], k=1)[0]


def _duration(rng: random.Random, median_seconds: float, sigma: float = 0.35) -> float:
    return median_seconds * rng.lognormvariate(0, sigma)


def events_for(job: SimJob, org_id: uuid.UUID, rng: random.Random) -> list[dict[str, Any]]:
    """The timeline of one job, ending where the job ends."""
    scale = min(4.0, 1 + math.log10(1 + job.records) / 3)
    seconds = {
        "discover": _duration(rng, 8 * scale), "profile": _duration(rng, 12 * scale), "map": _duration(rng, 9 if job.tokens else 3), "rules": _duration(rng, 2),
        "dry_run": _duration(rng, 6 * scale),
    }  # fmt: skip
    waits = {"after_map": rng.uniform(60, 3_600 * 3), "before_load": rng.uniform(30, 1_200)}  # a person reviews, then starts the load
    fail_stage = None if job.ok else _pick(rng, FAIL_STAGES)
    load_seconds = max(1.0, job.duration_ms / 1000)

    # build forward from t0 so that the load starts when the job's own record says it did
    total_before = sum(seconds.values()) + sum(waits.values()) + 2
    t = job.started - timedelta(seconds=total_before)
    events: list[dict[str, Any]] = []

    def add(frm: str | None, to: str, message: str | None, at: Any, detail: dict[str, Any] | None = None) -> None:
        events.append({"org_id": org_id, "job_id": job.id, "from_status": frm, "to_status": to, "message": message, "detail": detail or {}, "created_at": at})

    def stage(name: str, status: str, begin: Any, length: float, *, move: tuple[str, str] | None = None) -> Any:
        add(status, status, f"Stage {name} started", begin)
        finish = begin + timedelta(seconds=length)
        add(status, status, f"Stage {name} finished", finish, {"stage": name, "outcome": "done"})
        if move:
            add(move[0], move[1], f"{name} complete", finish)
        return finish

    def fail(name: str, frm: str, at: Any) -> list[dict[str, Any]]:
        add(frm, "FAILED", f"Stage {name} failed (RuntimeError)", at, {"stage": name, "error": "RuntimeError"})
        return events

    add("CREATED", "UPLOADED", "Source uploaded", t)
    t += timedelta(seconds=1)
    add("UPLOADED", "DISCOVERING", "Discovery started", t)
    if fail_stage == "discover":
        add("DISCOVERING", "DISCOVERING", "Stage discover started", t)
        return fail("discover", "DISCOVERING", t + timedelta(seconds=seconds["discover"] * 0.6))
    t = stage("discover", "DISCOVERING", t, seconds["discover"])
    if fail_stage == "profile":
        add("DISCOVERING", "DISCOVERING", "Stage profile started", t)
        return fail("profile", "DISCOVERING", t + timedelta(seconds=seconds["profile"] * 0.5))
    t = stage("profile", "DISCOVERING", t, seconds["profile"], move=("DISCOVERING", "PROFILED"))
    if fail_stage == "map":
        add("PROFILED", "PROFILED", "Stage map started", t)
        return fail("map", "PROFILED", t + timedelta(seconds=seconds["map"] * 0.7))
    t = stage("map", "PROFILED", t, seconds["map"], move=("PROFILED", "MAPPED"))
    t = stage("rules", "MAPPED", t, seconds["rules"], move=("MAPPED", "AWAITING_REVIEW"))
    t += timedelta(seconds=waits["after_map"])  # a person reviews
    add("AWAITING_REVIEW", "DRY_RUN", "Dry run on a sample", t)
    t = stage("dry_run", "DRY_RUN", t, seconds["dry_run"], move=("DRY_RUN", "READY"))
    t += timedelta(seconds=waits["before_load"])
    start = job.started  # the timeline was built to arrive here
    add("READY", "LOADING", "Loading into staging tables", start)
    add("LOADING", "LOADING", "Stage load started", start)
    if not job.ok:
        return fail("load", "LOADING", start + timedelta(seconds=load_seconds * 0.4))
    finish = start + timedelta(seconds=load_seconds)
    add("LOADING", "LOADING", "Stage load finished", finish, {"stage": "load", "outcome": "done"})
    add("LOADING", "RECONCILING", "Reconciling the loaded data", finish)
    add("RECONCILING", "COMPLETED", "Load and reconciliation finished", finish + timedelta(seconds=1), {"loaded": job.records})
    return events


def columns_for(job: SimJob, org_id: uuid.UUID, ordinal: int, binary: bool, plan: str, rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The columns of a completed job and how each was mapped. `ordinal` counts the organization's earlier completed jobs."""
    low, high = {"starter": (8, 12), "professional": (10, 16), "enterprise": (14, 24)}.get(plan, (8, 14))
    p_memory = min(0.82, 0.04 + 0.2 * math.log1p(ordinal)) if ordinal > 0 else 0.0
    p_human = max(0.02, 0.18 - 0.03 * ordinal)
    p_llm = 0.12 if job.tokens > 0 else 0.0
    columns, mappings = [], []
    for i in range(rng.randint(low, high)):
        cid = _uuid(rng)
        confidence = 1.0 if not binary else round(min(1.0, max(0.45, rng.betavariate(8, 2))), 3)
        columns.append({"id": cid, "org_id": org_id, "job_id": job.id, "table_name": f"TBL{i % 5}", "column_name": f"col_{i}", "pii": False, "profile": {}, "discovery_confidence": confidence, "created_at": job.started})
        roll = rng.random()
        method = "memory" if roll < p_memory else "human" if roll < p_memory + p_human else "llm" if roll < p_memory + p_human + p_llm else "embedding"
        mappings.append({
            "id": _uuid(rng), "org_id": org_id, "job_id": job.id, "source_column_id": cid, "target_field_id": None, "method": method, "status": "approved",
            "confidence": round(rng.uniform(0.62, 0.99), 3), "decision": "memory" if method == "memory" else "auto", "candidates": [], "created_at": job.started,
        })  # fmt: skip
    return columns, mappings


def quarantine_for(job: SimJob, org_id: uuid.UUID, rng: random.Random) -> list[dict[str, Any]]:
    """A sample of why rows were quarantined, a few per job (the real table holds every row; the history keeps the reasons)."""
    return [
        {"id": _uuid(rng), "org_id": org_id, "job_id": job.id, "target_table": rng.choice(TARGET_TABLES), "source_row_number": rng.randint(1, max(2, job.source_rows)),
         "reason_code": _pick(rng, QUARANTINE_REASONS), "reason_detail": "simulated", "raw_row": {"simulated": True}, "created_at": job.completed}
        for _ in range(rng.randint(2, 8))
    ]  # fmt: skip


def operational_rows(history: History, org_plans: dict[int, str], rng: random.Random) -> dict[str, list[dict[str, Any]]]:
    """Everything for every job of the history, as rows ready to insert."""
    out: dict[str, list[dict[str, Any]]] = {"job_events": [], "source_columns": [], "mappings": [], "quarantined_rows": []}
    completed_before: dict[int, int] = {}
    for job in history.jobs:
        org = history.orgs[job.org]
        out["job_events"].extend(events_for(job, org.id, rng))
        if not job.ok:
            continue
        ordinal = completed_before.get(job.org, 0)
        completed_before[job.org] = ordinal + 1
        columns, mappings = columns_for(job, org.id, ordinal, job.org % 3 == 0, org_plans.get(job.org, "starter"), rng)
        out["source_columns"].extend(columns)
        out["mappings"].extend(mappings)
        out["quarantined_rows"].extend(quarantine_for(job, org.id, rng))
    return out
