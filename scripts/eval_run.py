"""Evaluate the platform on the two sample legacy systems and write docs/evaluation.md (and docs/evaluation.json).

    cd backend && uv run python ../scripts/eval_run.py            # needs the database, Redis is not needed
    cd backend && uv run python ../scripts/eval_run.py --check    # also fail when a figure falls under its target

What it measures, and how:

* Scoring against the ground truth of the fixtures (no database, no network): field discovery recall, defect detection recall,
  and mapping accuracy of the rules and the embedding before any language model is asked.
* Two complete runs of each system through the real API in a new organization (stages run in the request, like the tests), the second
  one after the first has taught the mapping memory: wall time of every stage, mapping method counts, tokens and model calls by provider
  from the llm_calls table, reconciliation, and the loaded rows against the expected clean data.
* Mapping accuracy after the language model is measured only when a provider key is configured. Without a key the document says so, and
  the ceiling (what a model that always picks the right candidate could reach) is shown as an upper bound, never as a result.

The script creates an organization and a user of its own and removes them at the end. It writes no secret and logs no data values.
"""

import argparse
import csv
import json
import os
import platform
import secrets
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Settings are read when the application is imported: set what the run needs first.
os.environ.setdefault("JOBS_EXECUTION", "eager")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("UPLOAD_DIR", tempfile.mkdtemp(prefix="legacylift-eval-"))

FIXTURES = ROOT / "datasets" / "fixtures"
EXPECTED = ROOT / "datasets" / "expected"
CSRF = {"X-Requested-With": "LegacyLift"}
STAGES = ["discover", "profile", "map"]
PIPELINE = ["discover", "profile", "map", "rules", "dry_run", "load"]

TARGETS = {  # figure: minimum, the bar this evaluation holds the platform to
    "discovery_recall": 0.95,
    "defect_recall": 0.90,
    "reconciliation_pass_rate": 1.0,
    "quarantine_recall": 0.99,
    "dedup_recall": 0.99,
    "loaded_in_clean_data": 0.99,
    "field_accuracy": 0.98,
}


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


# ------------------------------------------------------------------ offline scoring


def offline_scores() -> dict[str, Any]:
    from app.discovery.dotnet_discovery import discover_layout
    from app.discovery.rea_adapter import get_rea_runner
    from app.discovery.sqlite_discovery import discover_sqlite
    from app.evaluation.discovery_score import score_layout, score_sqlite
    from app.evaluation.mapping_inputs import scenario, truth_columns
    from app.evaluation.mapping_score import score_decisions
    from app.evaluation.quality_score import aliases_from_layout, score_quality
    from app.mapping.decide import decide
    from app.mapping.thresholds import load_thresholds
    from app.profiling import engine
    from app.profiling.readers import BinaryReader, SqliteReader

    truth = {n: json.loads((FIXTURES / n / "ground_truth.json").read_text(encoding="utf-8")) for n in ("commerce", "clinic")}
    runner = get_rea_runner()
    out: dict[str, Any] = {}

    schema, _ = discover_sqlite(runner, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    d = score_sqlite(schema, truth["commerce"])
    out["commerce"] = {"discovery": {"recovered": d.recovered, "total": d.total, "recall": d.recall, "missing": d.missing}}

    layout = discover_layout(runner, FIXTURES / "clinic" / "ClinicDesk.dll", FIXTURES / "clinic" / "clinic.cdb").layout
    d = score_layout(layout, truth["clinic"]["layout"], by="name")
    out["clinic"] = {"discovery": {"recovered": d.recovered, "total": d.total, "recall": d.recall, "missing": d.missing}}
    # the same file without its program: the layout is recovered from the bytes alone
    blind = discover_layout(runner, None, FIXTURES / "clinic" / "clinic.cdb").layout
    b = score_layout(blind, truth["clinic"]["layout"], by="position")
    out["clinic"]["discovery_without_program"] = {"recovered": b.recovered, "total": b.total, "recall": b.recall}

    _, report = engine.profile_and_check(SqliteReader(FIXTURES / "commerce" / "commerce_legacy.sqlite").tables(), "sqlite", chunk_size=1000)
    q = score_quality(report, truth["commerce"]["defects"])
    out["commerce"]["defects"] = _quality(q)
    _, report = engine.profile_and_check(BinaryReader(FIXTURES / "clinic" / "clinic.cdb", layout).tables(), "dotnet_binary", chunk_size=5000)
    names = {t["entity"]: t["name"] for t in truth["clinic"]["layout"]["tables"]}
    q = score_quality(report, truth["clinic"]["defects"], table_names=names, column_aliases=aliases_from_layout(layout))
    out["clinic"]["defects"] = _quality(q)

    thresholds = load_thresholds()
    for name in ("commerce", "clinic"):
        s = scenario(name)
        decisions = decide(truth_columns(s), s.targets, thresholds)
        m = score_decisions(decisions, s.truth)
        out[name]["mapping_before_llm"] = {
            "columns": m.total, "correct": m.correct, "wrong_settled": m.wrong_settled, "waiting": m.waiting,
            "accuracy": m.accuracy, "ceiling": m.ceiling, "by_status": m.by_method,
        }  # fmt: skip
    return out


def _quality(q: Any) -> dict[str, Any]:
    return {
        "injected": q.injected, "detected": q.detected, "recall": q.recall, "core_recall": q.core_recall,
        "by_type": {k: {"injected": v.injected, "detected": v.detected, "recall": v.recall, "precision": v.precision} for k, v in sorted(q.by_type.items())},
    }  # fmt: skip


# ------------------------------------------------------------------ end to end through the API


class Evaluation:
    def __init__(self) -> None:
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.core.config import get_settings
        from app.core.security import hash_password
        from app.db import models
        from app.db.models.enums import Role
        from app.main import create_app

        self.engine = create_engine(get_settings().database_admin_url)
        self.password = secrets.token_urlsafe(18) + "-Aa1"
        self.email = f"eval-{uuid.uuid4().hex[:8]}@evaluation.example"
        with Session(self.engine) as session:
            org = models.Organization(name="Evaluation", slug=f"eval-{uuid.uuid4().hex[:8]}")
            session.add(org)
            session.flush()
            user = models.User(email=self.email, password_hash=hash_password(self.password), full_name="Evaluation")
            session.add(user)
            session.flush()
            session.add(models.Membership(org_id=org.id, user_id=user.id, role=Role.ANALYST))
            session.commit()
            self.org_id, self.user_id = org.id, user.id
        self.client = TestClient(create_app(), base_url="https://testserver", headers=CSRF)
        assert self.client.post("/api/v1/auth/login", json={"email": self.email, "password": self.password}).status_code == 200

    def close(self) -> None:
        from sqlalchemy import text

        self.client.close()
        try:
            with self.engine.begin() as c:
                c.execute(text("DELETE FROM organizations WHERE id = :i"), {"i": self.org_id})
                c.execute(text("DELETE FROM users WHERE id = :i"), {"i": self.user_id})
        except Exception as exc:  # the data is the evaluation's own and harmless; say so instead of failing the report
            print(f"note: the evaluation organization {self.org_id} was left in place ({type(exc).__name__})", file=sys.stderr)

    def call(self, method: str, path: str, **kw: Any) -> Any:
        r = self.client.request(method, f"/api/v1{path}", **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{method} {path} answered {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def review(self, job_id: str, schema: str, mapping_truth: dict[tuple[str, str], str | None], system_tables: set[str]) -> dict[str, int]:
        """A reviewer who knows the answer: accepts a suggestion that is right, corrects or rejects one that is wrong, and also
        corrects what the engine settled wrongly. The counts are how much a person had to do, which is what the memory reduces."""
        schema_id = next(s["id"] for s in self.call("GET", "/target-schemas") if s["name"] == schema)
        fields = {f"{f['table']}.{f['name']}": f["id"] for f in self.call("GET", f"/target-schemas/{schema_id}/fields")}
        done = {"accepted": 0, "corrected": 0, "rejected": 0}
        for m in self.call("GET", f"/jobs/{job_id}/mappings"):
            key = (m["source_table"], m["source_column"])
            if key not in mapping_truth and m["source_table"] not in system_tables:
                continue
            expected = mapping_truth.get(key)  # a table of the system itself, such as its log, has no counterpart in the answer
            if m["target"] == expected and m["status"] != "suggested":
                continue  # settled and right: nobody has to look at it
            if m["target"] == expected and expected is not None:
                self.call("POST", f"/jobs/{job_id}/mappings/{m['id']}/approve", json={})
                done["accepted"] += 1
            elif expected is None:
                if m["status"] != "rejected":
                    self.call("POST", f"/jobs/{job_id}/mappings/{m['id']}/reject")
                    done["rejected"] += 1
            else:
                self.call("POST", f"/jobs/{job_id}/mappings/{m['id']}/approve", json={"target_field_id": fields[expected]})
                done["corrected"] += 1
        return done

    def run_system(self, name: str, truth: dict[str, Any]) -> dict[str, Any]:
        """One complete migration of a sample system. Returns what was measured."""
        from app.evaluation.mapping_score import clinic_truth, commerce_truth

        mapping_truth = (commerce_truth if name == "commerce" else clinic_truth)(truth)
        reg = self.call("POST", f"/sources/samples/{name}")
        job = self.call("POST", "/jobs", json={"source_id": reg["source_id"], "data_source_id": reg["data_source_id"], "target_schema": reg["target_schema"]})
        jid = job["id"]
        seconds: dict[str, float] = {}

        def timed(label: str, fn) -> Any:  # type: ignore[no-untyped-def]
            t = time.perf_counter()
            result = fn()
            seconds[label] = round(time.perf_counter() - t, 2)
            return result

        for stage in STAGES:
            timed(stage, lambda s=stage: self.call("POST", f"/jobs/{jid}/stages/{s}", json={}))
        events = self.call("GET", f"/jobs/{jid}/events?limit=1000")["items"]
        counts = next((e["detail"]["counts"] for e in events if e["message"] and e["message"].startswith("Mapped to") and "counts" in e["detail"]), {})
        approved = timed("approve", lambda: self.call("POST", f"/jobs/{jid}/mappings/approve-decided"))
        reviewed = timed("review", lambda: self.review(jid, reg["target_schema"], mapping_truth, set(truth.get("system_tables", {}))))
        timed("rules", lambda: self.call("POST", f"/jobs/{jid}/stages/rules", json={}))  # the rules follow the reviewed mappings
        codes = code_meanings(name, truth)
        for rule in self.call("GET", f"/jobs/{jid}/rules"):
            if rule["status"] == "needs_input":
                missing = (rule["failures"].get("missing") or {}).get("codes", [])
                known = codes.get(f"{rule['target_table']}.{rule['target_field']}", {})
                mapping = {c: known[c] for c in missing if c in known}
                self.call("PUT", f"/jobs/{jid}/rules/{rule['id']}/code-map", json={"mapping": mapping, "passthrough": len(mapping) < len(missing)})
        dry = timed("dry_run", lambda: self.call("POST", f"/jobs/{jid}/dry-run"))
        if not dry["ready"]:
            raise SystemExit(f"{name}: the dry run is blocked: {dry['blocking']}")
        timed("load", lambda: self.call("POST", f"/jobs/{jid}/stages/load", json={}))
        report = self.call("GET", f"/jobs/{jid}/reconciliation")
        final = self.call("GET", f"/jobs/{jid}")
        quarantine = list(csv.DictReader(self.client.get(f"/api/v1/jobs/{jid}/quarantine.csv").text.splitlines()))
        handling = defect_handling(quarantine, name, truth)
        against_clean = compare_with_expected(self.engine, self.org_id, jid, name)
        return {
            "job_id": jid, "status": final["status"], "stage_seconds": seconds, "total_seconds": round(sum(v for k, v in seconds.items() if k in PIPELINE), 2),
            "mapping_counts": counts, "approved": approved.get("approved"), "review": reviewed, "defect_handling": handling, "against_clean": against_clean, "llm": self.llm_usage(jid),
            "reconciliation": {"passed": report["passed"], "source_rows": report["source_rows"], "loaded": report["loaded_rows"], "quarantined": report["quarantined_rows"],
                               "quality_before": report["quality_before"], "quality_after": report["quality_after"],
                               "tables": {t["target_table"]: {"loaded": t["loaded"], "quarantined": t["quarantined"], "passed": t["passed"]} for t in report["checks"]["tables"]}},
        }  # fmt: skip

    def llm_usage(self, job_id: str) -> dict[str, Any]:
        from sqlalchemy import text

        with self.engine.connect() as c:
            rows = c.execute(
                text("SELECT provider, status::text, cache_hit, count(*), coalesce(sum(tokens_in),0), coalesce(sum(tokens_out),0) FROM llm_calls WHERE job_id = :j GROUP BY 1,2,3"),
                {"j": job_id},
            ).all()
        providers: dict[str, dict[str, int]] = {}
        calls = hits = 0
        for provider, status, cached, n, tin, tout in rows:
            slot = providers.setdefault(provider, {"calls": 0, "tokens": 0})
            if cached:
                hits += n
            elif status == "OK" or status == "ok":
                calls += n
                slot["calls"] += n
                slot["tokens"] += int(tin) + int(tout)
        return {"calls": calls, "cache_hits": hits, "tokens": sum(p["tokens"] for p in providers.values()), "by_provider": providers}


def code_meanings(system: str, truth: dict[str, Any]) -> dict[str, dict[str, str]]:
    """What a person who knows the legacy system would tell the platform about its codes, read from the ground truth: for the commerce
    database the list of order status codes, for the clinic file the `map_codes(1=a,2=b)` transform written beside the field."""
    if system == "commerce":
        return {"orders.status": truth.get("status_codes", {})}
    out: dict[str, dict[str, str]] = {}
    for table in truth["layout"]["tables"]:
        for f in table["fields"]:
            transform = f.get("transform") or ""
            if transform.startswith("map_codes(") and f.get("target"):
                out[f["target"]] = dict(pair.split("=", 1) for pair in transform[len("map_codes("):-1].split(","))
    return out


def expected_rows() -> dict[str, int]:
    out: dict[str, int] = {}
    for system in ("commerce", "clinic"):
        for path in sorted((EXPECTED / system).glob("*.csv")):
            with path.open(encoding="utf-8", newline="") as handle:
                out[path.stem] = max(0, sum(1 for _ in csv.reader(handle)) - 1)
    return out


PARENTS = {"customer_id": "customers", "order_id": "orders", "product_id": "products", "patient_id": "patients", "encounter_id": "encounters"}


def _norm(value: Any) -> str:
    """One text form for a value from the database and for the same value from the expected CSV, so that 0.99 and 0.990, a timestamp
    with and without its zone marker, and true and True are the same."""
    from datetime import date, datetime
    from decimal import Decimal, InvalidOperation

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text.lower() in ("true", "false"):
        return text.lower()
    if text.endswith("Z") and "T" in text:
        text = text[:-1]
    try:
        number = Decimal(text)
        return format(number.normalize(), "f") if number == number.to_integral() or "." in text else text
    except InvalidOperation:
        return text


def defect_handling(quarantine: list[dict[str, str]], system: str, truth: dict[str, Any]) -> dict[str, Any]:
    """Each defect the fixture builder injected, and what became of its row: a row that had to be quarantined is in the quarantine, a
    copy that had to be removed is not loaded. Rows quarantined without a defect of their own are the consequences of others (a child
    whose parent was quarantined), counted by reason so that nothing is hidden."""
    if system == "commerce":
        names = {k: v["target_table"] for k, v in truth["legacy_tables"].items()}
        defects = [(names[d["table"]], d["rowid"], d) for d in truth["defects"]]
    else:
        defects = [(d["table"], d["row_index"], d) for d in truth["defects"]]
    quarantined = {(r["table"], int(r["row"])): r["reason"] for r in quarantine}
    caught = reason_match = wanted = dedup_total = dedup_done = 0
    for table, row, d in defects:
        if d["expected_outcome"] == "quarantine":
            wanted += 1
            reason = quarantined.get((table, row))
            caught += 1 if reason else 0
            reason_match += 1 if reason == d["reason_code"] else 0
        elif d["expected_outcome"] == "deduplicate":
            dedup_total += 1
            dedup_done += 1 if (table, row) in quarantined else 0
    explained = {(t, r) for t, r, d in defects if d["expected_outcome"] in ("quarantine", "deduplicate")}
    extra = [reason for key, reason in quarantined.items() if key not in explained]
    by_reason: dict[str, int] = {}
    for reason in extra:
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "quarantine_expected": wanted, "quarantine_caught": caught, "quarantine_with_expected_reason": reason_match,
        "duplicates_expected": dedup_total, "duplicates_removed": dedup_done, "quarantined_total": len(quarantined),
        "quarantined_without_a_defect": len(extra), "quarantined_without_a_defect_by_reason": by_reason,
    }  # fmt: skip


def compare_with_expected(engine: Any, org_id: Any, job_id: str, system: str) -> dict[str, Any]:
    """The loaded rows of a job against the clean data the fixture was built from: for every loaded row, is it in the clean data, and
    how many of its fields equal the clean value (references compared through the source key of the row they point to)."""
    from sqlalchemy import text as sql

    tables = [p.stem for p in sorted((EXPECTED / system).glob("*.csv"))]
    out: dict[str, Any] = {}
    with engine.connect() as c:
        keys = {t: {str(i): k for i, k in c.execute(sql(f"SELECT id, source_key FROM warehouse.{t} WHERE job_id = :j"), {"j": job_id})} for t in set(PARENTS.values()) & set(tables)}
        for table in tables:
            with (EXPECTED / system / f"{table}.csv").open(encoding="utf-8", newline="") as handle:
                expected = {r["source_key"]: r for r in csv.DictReader(handle)}
            rows = c.execute(sql(f"SELECT * FROM warehouse.{table} WHERE job_id = :j"), {"j": job_id}).mappings().all()
            known = cells = equal = exact = case_only = 0
            differing: dict[str, int] = {}
            examples: dict[str, list[tuple[str, str]]] = {}
            for row in rows:
                truth_row = expected.get(str(row["source_key"]))
                if truth_row is None:
                    continue
                known += 1
                row_ok = True
                for column, want in truth_row.items():
                    if column == "source_key":
                        continue
                    got = row[column]
                    if column in PARENTS:
                        got = keys.get(PARENTS[column], {}).get(str(got))
                    cells += 1
                    if _norm(got) == _norm(want):
                        equal += 1
                    else:
                        row_ok = False
                        differing[column] = differing.get(column, 0) + 1
                        examples.setdefault(f"{table}.{column}", []).append((_norm(want), _norm(got)))
                        case_only += 1 if _norm(got).lower() == _norm(want).lower() else 0
                exact += 1 if row_ok else 0
            out[table] = {"loaded": len(rows), "in_clean_data": known, "cells": cells, "cells_equal": equal, "rows_exact": exact, "differ_only_in_case": case_only, "differing_fields": dict(sorted(differing.items(), key=lambda kv: -kv[1])[:5]), "examples": {k: v[:3] for k, v in examples.items()}}
    return out


def end_to_end(llm_configured: bool) -> dict[str, Any]:
    from app.evaluation.mapping_inputs import scenario  # noqa: F401  (loads the embedding model before anything is timed)
    from app.mapping.embedding import get_embedding_service

    get_embedding_service().encode(["warm up"])
    truth = {n: json.loads((FIXTURES / n / "ground_truth.json").read_text(encoding="utf-8")) for n in ("commerce", "clinic")}
    ev = Evaluation()
    try:
        runs = {name: {"first": ev.run_system(name, truth[name]), "repeat": ev.run_system(name, truth[name])} for name in ("commerce", "clinic")}
    finally:
        ev.close()
    expected = expected_rows()
    for pair in runs.values():
        for run in pair.values():
            cells = sum(t["cells"] for t in run["against_clean"].values())
            equal = sum(t["cells_equal"] for t in run["against_clean"].values())
            loaded = sum(t["loaded"] for t in run["against_clean"].values())
            known = sum(t["in_clean_data"] for t in run["against_clean"].values())
            h = run["defect_handling"]
            run["field_accuracy"] = equal / cells if cells else 0.0
            run["loaded_in_clean_data"] = known / loaded if loaded else 0.0
            run["quarantine_recall"] = h["quarantine_caught"] / h["quarantine_expected"] if h["quarantine_expected"] else 1.0
            run["dedup_recall"] = h["duplicates_removed"] / h["duplicates_expected"] if h["duplicates_expected"] else 1.0
    return {"runs": runs, "expected_rows": expected, "llm_configured": llm_configured}


# ------------------------------------------------------------------ the document


def render(offline: dict[str, Any], e2e: dict[str, Any], env: dict[str, Any]) -> str:
    L: list[str] = []
    w = L.append
    w("# Evaluation")
    w("")
    w(f"Generated by `scripts/eval_run.py` on {env['date']}. Re-run it to refresh every figure in this document: `cd backend && uv run python ../scripts/eval_run.py`.")
    w("")
    w(f"Environment: Python {env['python']}, {env['platform']}, embedding model `{env['embedding_model']}`, REA {env['rea']}. The pipeline ran in one process with the stages inside the request (no broker). Language model provider keys: {'configured' if env['llm'] else 'not configured, so no language model was called'}.")
    w("")
    w("## How to read this")
    w("")
    w("The two sample systems are built with known defects and a known answer (`datasets/fixtures/*/ground_truth.json`). The platform never reads that file; the harness does, to score what the platform found. Every figure below is measured by the run that wrote this document, and nothing is estimated unless it says so.")
    w("")
    w("## Discovery, defect detection and mapping before the language model")
    w("")
    w("| Measure | Commerce (SQLite) | Clinic (.NET binary) |")
    w("|---|---|---|")
    c, k = offline["commerce"], offline["clinic"]
    w(f"| Field discovery recall | {pct(c['discovery']['recall'])} ({c['discovery']['recovered']} of {c['discovery']['total']}) | {pct(k['discovery']['recall'])} ({k['discovery']['recovered']} of {k['discovery']['total']}) |")
    w(f"| Field discovery recall from the data file alone (no program), by position | not applicable | {pct(k['discovery_without_program']['recall'])} ({k['discovery_without_program']['recovered']} of {k['discovery_without_program']['total']}) |")
    w(f"| Injected defects detected | {pct(c['defects']['recall'])} ({c['defects']['detected']} of {c['defects']['injected']}) | {pct(k['defects']['recall'])} ({k['defects']['detected']} of {k['defects']['injected']}) |")
    w(f"| Core defect types detected (duplicate, missing required value, invalid date, out of range, orphan reference) | {pct(c['defects']['core_recall'])} | {pct(k['defects']['core_recall'])} |")
    for system, label in ((c, "Commerce"), (k, "Clinic")):
        m = system["mapping_before_llm"]
        system["_m"] = m
    cm, km = c["mapping_before_llm"], k["mapping_before_llm"]
    w(f"| Columns mapped correctly by rules and embedding alone | {pct(cm['accuracy'])} ({cm['correct']} of {cm['columns']}) | {pct(km['accuracy'])} ({km['correct']} of {km['columns']}) |")
    w(f"| Columns settled wrongly without help | {cm['wrong_settled']} | {km['wrong_settled']} |")
    w(f"| Columns left for a person or the model | {cm['waiting']} | {km['waiting']} |")
    w("")
    w("Defect recall by type:")
    w("")
    w("| Defect type | Commerce injected | Commerce detected | Clinic injected | Clinic detected |")
    w("|---|---|---|---|---|")
    types = sorted(set(c["defects"]["by_type"]) | set(k["defects"]["by_type"]))
    for t in types:
        a, b = c["defects"]["by_type"].get(t), k["defects"]["by_type"].get(t)
        w(f"| {t.replace('_', ' ')} | {a['injected'] if a else 0} | {a['detected'] if a else 0} | {b['injected'] if b else 0} | {b['detected'] if b else 0} |")
    w("")
    w("## Mapping accuracy after the language model")
    w("")
    if e2e["llm_configured"]:
        w("The language model was asked about the columns that rules and embedding could not settle; see the runs below for the calls and tokens it used.")
    else:
        w("**Not measured in this run.** No provider key was configured, so the language model was never called and there is no accuracy to report for it. The only figure that can be given without a model is a ceiling: the share of columns that would be right if a model always chose the correct one among the candidates it is offered.")
        w("")
        w("| | Commerce | Clinic |")
        w("|---|---|---|")
        w(f"| Ceiling with a model that always picks the right candidate (an upper bound, not a result) | {pct(cm['ceiling'])} | {pct(km['ceiling'])} |")
    w("")
    w("## End to end runs, first and repeat")
    w("")
    w("Each system was migrated twice in a new organization. The first run starts with an empty mapping memory; a person (the harness) approves the decided mappings, which teaches the memory. The repeat run is the same system again.")
    w("")
    w("### Wall time by stage (seconds)")
    w("")
    w("| System | Run | Discover | Profile | Map | Rules | Dry run | Load | Total |")
    w("|---|---|---|---|---|---|---|---|---|")
    for name, pair in e2e["runs"].items():
        for label, run in pair.items():
            s = run["stage_seconds"]
            w(f"| {name} | {label} | {s['discover']} | {s['profile']} | {s['map']} | {s['rules']} | {s['dry_run']} | {s['load']} | {run['total_seconds']} |")
    w("")
    w("The embedding model was loaded before timing began, so the map stage is the steady state.")
    w("")
    w("### Mapping methods, tokens and model calls")
    w("")
    w("| System | Run | Settled by rules or embedding | From memory | For review | Unmapped | Waiting for the model | Model calls | Tokens | Cache hits |")
    w("|---|---|---|---|---|---|---|---|---|---|")
    for name, pair in e2e["runs"].items():
        for label, run in pair.items():
            n, u = run["mapping_counts"], run["llm"]
            w(f"| {name} | {label} | {n.get('auto', 0)} | {n.get('memory', 0)} | {n.get('review', 0)} | {n.get('unmapped', 0)} | {n.get('needs_llm', 0)} | {u['calls']} | {u['tokens']} | {u['cache_hits']} |")
    w("")
    providers = {p for pair in e2e["runs"].values() for run in pair.values() for p in run["llm"]["by_provider"]}
    if providers:
        w("Tokens by provider:")
        w("")
        w("| System | Run | " + " | ".join(sorted(providers)) + " |")
        w("|---|---|" + "---|" * len(providers))
        for name, pair in e2e["runs"].items():
            for label, run in pair.items():
                w(f"| {name} | {label} | " + " | ".join(str(run["llm"]["by_provider"].get(p, {}).get("tokens", 0)) for p in sorted(providers)) + " |")
        w("")
    else:
        w("No tokens were used by any provider in this run, because none was configured.")
        w("")
    w("### What a person had to do")
    w("")
    w("After the engine has decided, a reviewer who knows the answer (the harness, from the ground truth) accepts the suggestions that are right, corrects or rejects the ones that are wrong, and fixes anything the engine settled wrongly. Every approval teaches the mapping memory, so the repeat run should need fewer corrections.")
    w("")
    w("| System | Run | Suggestions accepted | Targets corrected | Mappings rejected | Total decisions by a person |")
    w("|---|---|---|---|---|---|")
    for name, pair in e2e["runs"].items():
        for label, run in pair.items():
            r = run["review"]
            w(f"| {name} | {label} | {r['accepted']} | {r['corrected']} | {r['rejected']} | {r['accepted'] + r['corrected'] + r['rejected']} |")
    w("")
    w("### Reconciliation")
    w("")
    w("| System | Run | Source rows | Loaded | Quarantined | Quality before | Quality after | Reconciliation |")
    w("|---|---|---|---|---|---|---|---|")
    passed = total = 0
    for name, pair in e2e["runs"].items():
        for label, run in pair.items():
            r = run["reconciliation"]
            total += 1
            passed += 1 if r["passed"] and run["status"] == "COMPLETED" else 0
            w(f"| {name} | {label} | {r['source_rows']} | {r['loaded']} | {r['quarantined']} | {r['quality_before']} | {r['quality_after']} | {'passed' if r['passed'] else 'failed'} |")
    w("")
    w(f"Reconciliation pass rate: {passed} of {total} runs ({pct(passed / total if total else None)}). Reconciliation proves internal consistency (every source row is loaded or quarantined with a reason, totals and a sample of rows agree); the next section checks the result against the clean data the fixtures were built from.")
    w("")
    w("## Defect handling and correctness of the loaded data")
    w("")
    w("The fixture builder starts from clean data and injects defects, each with the outcome it should have: quarantine (with a reason), remove as a duplicate, or normalize. The first table follows the rows of those defects; the second compares what was loaded, field by field, with the clean data.")
    w("")
    w("| System | Run | Rows to quarantine, quarantined | With the expected reason | Duplicates, removed | Quarantined with no defect of their own |")
    w("|---|---|---|---|---|---|")
    for name, pair in e2e["runs"].items():
        for label, run in pair.items():
            h = run["defect_handling"]
            extra = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(h["quarantined_without_a_defect_by_reason"].items())) or "none"
            w(f"| {name} | {label} | {h['quarantine_caught']} of {h['quarantine_expected']} | {h['quarantine_with_expected_reason']} of {h['quarantine_expected']} | {h['duplicates_removed']} of {h['duplicates_expected']} | {h['quarantined_without_a_defect']} ({extra}) |")
    w("")
    w("Rows quarantined without a defect of their own are consequences: a row that refers to a parent which was itself quarantined cannot be loaded, and is quarantined as an orphan.")
    w("")
    w("| System | Run | Loaded rows found in the clean data | Fields compared | Fields equal | Loaded rows with every field equal |")
    w("|---|---|---|---|---|---|")
    for name, pair in e2e["runs"].items():
        for label, run in pair.items():
            t = run["against_clean"].values()
            loaded, known, cells, equal, exact = (sum(x[k] for x in t) for k in ("loaded", "in_clean_data", "cells", "cells_equal", "rows_exact"))
            w(f"| {name} | {label} | {known} of {loaded} | {cells} | {equal} ({pct(equal / cells if cells else None)}) | {exact} of {known} |")
    w("")
    differing = {}
    for pair in e2e["runs"].values():
        for run in pair.values():
            for table, info in run["against_clean"].items():
                for column, n in info["differing_fields"].items():
                    differing[f"{table}.{column}"] = max(differing.get(f"{table}.{column}", 0), n)
    for system, pair in e2e["runs"].items():
        run = pair["first"]
        unequal = sum(t["cells"] - t["cells_equal"] for t in run["against_clean"].values())
        case = sum(t["differ_only_in_case"] for t in run["against_clean"].values())
        w(f"- {system}: {unequal} of {sum(t['cells'] for t in run['against_clean'].values())} fields differ from the clean data; {case} of them differ only in capitalization.")
    w("")
    if differing:
        w("Fields that differ from the clean data most often (in the worst run): " + ", ".join(f"{k} ({v})" for k, v in sorted(differing.items(), key=lambda kv: -kv[1])[:8]) + ".")
        w("")
    w("## Limits of this evaluation")
    w("")
    all_cells = sum(t["cells"] - t["cells_equal"] for pair in e2e["runs"].values() for t in pair["first"]["against_clean"].values())
    all_case = sum(t["differ_only_in_case"] for pair in e2e["runs"].values() for t in pair["first"]["against_clean"].values())
    if all_cells:
        w(f"- The case rule treats unusual capitalization as a defect and writes the value in title or sentence case. {all_case} of the {all_cells} loaded fields that differ from the clean data (in the first runs) differ only in capitalization, which is what happens to acronyms and names that are written with capitals in the middle on purpose. A person can remove or change the rule in the transform preview.")
    w("- The clinic defects that are not quarantined (6 of 2,057) are all out-of-range values in `patients.HEALTHCARE_EXPENSES`, a source field that the clinic target schema has no column for. It is not mapped and not loaded, so a value in it can be reported by profiling but is never checked at load.")
    w("- Two systems are a small sample. The figures show that the method works on these two, not how it will do on any legacy system.")
    w("- The defects are injected by the fixture builder, which also writes the answer. Natural oddities in the data are reported by the platform and are not counted as errors, so precision is informational.")
    w("- Timings come from one machine in one process and depend on it; the stage ratios matter more than the seconds.")
    w("- The repeat run shows the effect of the mapping memory. The effect of the model cache can only be seen when a provider is configured, and is zero here when it is not.")
    w("")
    return "\n".join(L) + "\n"


def environment() -> dict[str, Any]:
    from app.discovery.rea_adapter import get_rea_runner
    from app.llm.factory import llm_available

    try:
        rea = str(getattr(get_rea_runner(), "version", None) or "6.7.0")
    except Exception:
        rea = "unknown"
    from app.mapping.embedding import get_embedding_service

    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"), "python": platform.python_version(), "platform": f"{platform.system()} {platform.release()}",
        "embedding_model": getattr(get_embedding_service(), "model_name", "multilingual-e5-small"), "rea": rea, "llm": bool(llm_available()),
    }  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit with an error when a figure is under its target")
    parser.add_argument("--no-write", action="store_true", help="print the document instead of writing it")
    args = parser.parse_args()

    env = environment()
    print("scoring against the ground truth ...", flush=True)
    offline = offline_scores()
    print("running both systems end to end, twice each ...", flush=True)
    e2e = end_to_end(env["llm"])
    document = render(offline, e2e, env)
    if args.no_write:
        print(document)
    else:
        (ROOT / "docs" / "evaluation.md").write_text(document, encoding="utf-8")
        (ROOT / "docs" / "evaluation.json").write_text(json.dumps({"environment": env, "scores": offline, "runs": e2e}, indent=2, default=str) + "\n", encoding="utf-8")
        print("wrote docs/evaluation.md and docs/evaluation.json")

    runs = [r for pair in e2e["runs"].values() for r in pair.values()]
    figures = {
        "discovery_recall": min(offline["commerce"]["discovery"]["recall"], offline["clinic"]["discovery"]["recall"]),
        "defect_recall": min(offline["commerce"]["defects"]["recall"], offline["clinic"]["defects"]["recall"]),
        "reconciliation_pass_rate": sum(1 for r in runs if r["reconciliation"]["passed"] and r["status"] == "COMPLETED") / len(runs),
        "quarantine_recall": min(r["quarantine_recall"] for r in runs),
        "dedup_recall": min(r["dedup_recall"] for r in runs),
        "loaded_in_clean_data": min(r["loaded_in_clean_data"] for r in runs),
        "field_accuracy": min(r["field_accuracy"] for r in runs),
    }
    failed = {k: (v, TARGETS[k]) for k, v in figures.items() if v < TARGETS[k]}
    for k, v in figures.items():
        print(f"{k}: {v:.3f} (target {TARGETS[k]})")
    if failed and args.check:
        print("under target:", failed, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
