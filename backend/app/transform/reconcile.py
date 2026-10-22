"""The reconciliation report: proof that what is in the warehouse is what the source said, and that every difference is explained.

Checks per target table
  * row accounting: source rows = rows staged = loaded + quarantined, so no row is lost and none is invented
  * distinct keys: the distinct legacy keys of the source = keys loaded + keys that only appear in quarantine
  * null counts: for every field, empty values in the warehouse = empty values among the rows that were accepted
  * numeric sums: for every numeric field, the exact sum in the warehouse = the sum of the transformed source values accepted
  * sampled rows: a deterministic sample of loaded rows (one in twenty by the hash of the legacy key) is read back from the
    warehouse and compared, field by field, with what the transformation produced
  * every quarantined row has a reason code

The expected values come from the staging pass (see load.py), which saw every row independently of the database moves, so a
mismatch means the load changed something it should not have.

The quality score before is the one the profiling phase reported; the score after is the same checks run on the loaded warehouse.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.profiling.engine import profile_and_check
from app.transform import warehouse_reader
from app.transform.engine import to_text
from app.transform.load import MoveResult, StageAggregates, distinct_staged_keys, ident, loaded_row_ids
from app.transform.plan import TablePlan

MAX_LISTED_MISMATCHES = 5


@dataclass
class TableReconciliation:
    target_table: str
    source_table: str
    source_rows: int
    staged_rows: int
    loaded: int
    quarantined: int
    by_reason: dict[str, int]
    checks: dict[str, Any] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    @property
    def unexplained(self) -> int:
        return self.source_rows - self.loaded - self.quarantined

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_table": self.target_table, "source_table": self.source_table, "source_rows": self.source_rows, "staged_rows": self.staged_rows,
            "loaded": self.loaded, "quarantined": self.quarantined, "by_reason": self.by_reason, "unexplained": self.unexplained,
            "checks": self.checks, "failed": self.failed, "passed": not self.failed,
        }  # fmt: skip


def _decimal_text(value: Any) -> str:
    return "0" if value is None else format(Decimal(str(value)), "f")


def reconcile_table(session: Session, plan: TablePlan, agg: StageAggregates, move: MoveResult, job_id: uuid.UUID) -> TableReconciliation:
    stg_rows = session.scalar(text(f"SELECT count(*) FROM {ident('stg_' + plan.target_table)}")) or 0
    warehouse_rows = session.scalar(text(f"SELECT count(*) FROM warehouse.{ident(plan.target_table)} WHERE job_id = :job"), {"job": job_id}) or 0
    quarantine_rows = session.scalar(text("SELECT count(*) FROM quarantined_rows WHERE job_id = :job AND target_table = :t"), {"job": job_id, "t": plan.target_table}) or 0
    rec = TableReconciliation(plan.target_table, plan.source_table, agg.source_rows, int(stg_rows), int(warehouse_rows), int(quarantine_rows), dict(move.by_reason))

    # row accounting
    rows_ok = agg.source_rows == stg_rows == warehouse_rows + quarantine_rows and warehouse_rows == move.loaded and quarantine_rows == move.quarantined
    rec.checks["row_accounting"] = {"source": agg.source_rows, "staged": int(stg_rows), "loaded": int(warehouse_rows), "quarantined": int(quarantine_rows), "ok": rows_ok}
    if not rows_ok:
        rec.failed.append("row_accounting")
    unreasoned = session.scalar(text("SELECT count(*) FROM quarantined_rows WHERE job_id = :job AND target_table = :t AND (reason_code IS NULL OR reason_code = '')"), {"job": job_id, "t": plan.target_table})
    rec.checks["every_quarantined_row_has_a_reason"] = {"without_reason": int(unreasoned or 0), "ok": not unreasoned}
    if unreasoned:
        rec.failed.append("every_quarantined_row_has_a_reason")

    # distinct keys
    source_keys = {k for k in agg.keys if k is not None}
    loaded_keys, quarantine_only = distinct_staged_keys(session, plan)
    warehouse_keys = session.scalar(text(f"SELECT count(DISTINCT source_key) FROM warehouse.{ident(plan.target_table)} WHERE job_id = :job"), {"job": job_id}) or 0
    keys_ok = len(source_keys) == loaded_keys + quarantine_only and warehouse_keys == loaded_keys == warehouse_rows
    rec.checks["distinct_keys"] = {"source": len(source_keys), "loaded": int(warehouse_keys), "only_in_quarantine": quarantine_only, "ok": keys_ok}
    if not keys_ok:
        rec.failed.append("distinct_keys")

    # expected values over the rows that were loaded
    all_ids = np.concatenate(agg.row_ids) if agg.row_ids else np.array([], dtype=np.int64)
    ids = loaded_row_ids(session, plan)
    mask = np.isin(all_ids, ids)

    nulls: dict[str, Any] = {}
    for rule in plan.rules:
        expected = int(np.concatenate(agg.nulls[rule.target_field])[mask].sum()) if agg.nulls[rule.target_field] else 0
        column = ident(rule.target_field if rule.target_field != "source_key" else "source_key")
        actual = int(session.scalar(text(f"SELECT count(*) FILTER (WHERE {column} IS NULL) FROM warehouse.{ident(plan.target_table)} WHERE job_id = :job"), {"job": job_id}) or 0)
        source_nulls = int(np.concatenate(agg.nulls[rule.target_field]).sum()) if agg.nulls[rule.target_field] else 0
        nulls[rule.target_field] = {"source_after_transform": source_nulls, "expected_loaded": expected, "loaded": actual, "ok": expected == actual}
    rec.checks["null_counts"] = nulls
    if not all(v["ok"] for v in nulls.values()):
        rec.failed.append("null_counts")

    sums: dict[str, Any] = {}
    for name, chunks in agg.numbers.items():
        flat = [v for chunk in chunks for v in chunk]
        values = np.asarray(flat, dtype=object)[mask] if flat else []
        expected_sum = sum((Decimal(str(v)) for v in values if v is not None), Decimal(0))
        actual_sum = session.scalar(text(f"SELECT sum({ident(name)}) FROM warehouse.{ident(plan.target_table)} WHERE job_id = :job"), {"job": job_id})
        sums[name] = {"expected": _decimal_text(expected_sum), "loaded": _decimal_text(actual_sum), "ok": expected_sum == Decimal(str(actual_sum or 0))}
    rec.checks["numeric_sums"] = sums
    if not all(v["ok"] for v in sums.values()):
        rec.failed.append("numeric_sums")

    # sampled rows
    wanted = {row_id: texts for row_id, texts in agg.sample.items() if row_id in set(ids.tolist())}
    keys = [t[plan.fields.index("source_key")] for t in wanted.values()]
    mismatches: list[dict[str, Any]] = []
    checked = matched = 0
    if keys:
        frame = warehouse_reader.read_rows(session, plan, job_id, keys)
        stored = {row["source_key"]: [to_text(row[f]) for f in plan.fields] for row in frame.to_dict("records")}
        for texts in wanted.values():
            key = texts[plan.fields.index("source_key")]
            checked += 1
            actual = stored.get(key)
            if actual is not None and list(texts) == actual:
                matched += 1
            elif len(mismatches) < MAX_LISTED_MISMATCHES:
                differing = [f for f, a, b in zip(plan.fields, texts, actual or [], strict=False) if a != b] if actual else ["row is missing"]
                mismatches.append({"source_key": key, "fields": differing})
    rec.checks["sampled_rows"] = {"checked": checked, "matched": matched, "mismatches": mismatches, "ok": checked == matched}
    if checked != matched:
        rec.failed.append("sampled_rows")
    return rec


def quality_after(session: Session, plans: list[TablePlan], job_id: uuid.UUID) -> dict[str, Any]:
    report = profile_and_check(warehouse_reader.tables(session, plans, job_id), "warehouse")[1]
    return {"score": report.score, "issue_counts": report.issue_counts, "tables": {t.table: t.score for t in report.tables}}
