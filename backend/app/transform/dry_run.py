"""Dry run: apply the plan to a sample of the source and show what a load would do, without touching the warehouse.

For every target table: the sample size, how many rows would be quarantined and why, per rule how many values were empty, failed or
fell out of range (and per step of the rule), and a before and after preview of rows, with some failing rows included so a reviewer
sees what goes wrong. Nothing here is stored except the failure counts on the rules.
"""

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.profiling.readers import TableSource
from app.transform.engine import RuleStats, to_text, transform_chunk
from app.transform.plan import Plan, RuleSpec, TablePlan

SAMPLE_ROWS = 500
PREVIEW_ROWS = 10
WARN_FAILURE_RATE = 0.2
BLOCK_FAILURE_RATE = 0.5


@dataclass
class RuleReport:
    target_field: str
    inputs: list[str]
    ops: list[str]
    rows: int
    nulls: int
    failures: int
    out_of_range: int
    step_failures: list[int]
    failure_rate: float


@dataclass
class TableDryRun:
    target_table: str
    source_table: str
    sample_rows: int
    would_load: int
    would_quarantine: dict[str, int]
    rules: list[RuleReport]
    preview: list[dict[str, Any]]
    source_rows: int | None = None  # rows in the whole source table
    seconds: float = 0.0  # what the sample took to transform


@dataclass
class DryRun:
    tables: list[TableDryRun] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blocking


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def dry_run_table(plan: TablePlan, source: TableSource, sample_rows: int = SAMPLE_ROWS) -> tuple[TableDryRun, dict[str, RuleStats]]:
    chunk = next(iter(source.chunks(sample_rows)), pd.DataFrame())
    if chunk.empty:
        return TableDryRun(plan.target_table, plan.source_table, 0, 0, {}, [], [], source.row_count, 0.0), {}
    started = time.perf_counter()
    result = transform_chunk(plan, chunk)
    seconds = time.perf_counter() - started
    counts: dict[str, int] = {}
    for reason in result.reasons.dropna():
        counts[reason] = counts.get(reason, 0) + 1
    reports = []
    for rule in plan.rules:
        s = result.stats[rule.target_field]
        present = max(1, s.rows - s.nulls + s.failures)
        reports.append(RuleReport(rule.target_field, list(rule.inputs), [step.spec.name for step in rule.steps], s.rows, s.nulls, s.failures, s.out_of_range,
                                  s.step_failures, round((s.failures + s.out_of_range) / present, 4)))  # fmt: skip
    clean = [i for i in result.reasons.index if result.reasons[i] is None][:PREVIEW_ROWS]
    failing = [i for i in result.reasons.index if result.reasons[i] is not None][:PREVIEW_ROWS]
    preview = []
    for index in clean + failing:
        before = {c: _json(chunk.at[index, c]) for c in plan.source_columns}
        after = {f: to_text(result.values.at[index, f]) for f in plan.fields}
        preview.append({"row": int(index), "before": before, "after": after, "reason": result.reasons[index], "detail": result.details[index]})
    return TableDryRun(plan.target_table, plan.source_table, len(chunk), len(chunk) - sum(counts.values()), counts, reports, preview, source.row_count, round(seconds, 4)), result.stats


def dry_run(plan: Plan, sources: dict[str, TableSource], sample_rows: int = SAMPLE_ROWS) -> tuple[DryRun, dict[tuple[str, str], RuleReport]]:
    run = DryRun(blocking=list(plan.issues), warnings=list(plan.warnings))
    by_rule: dict[tuple[str, str], RuleReport] = {}
    for table in plan.tables:
        source = sources.get(table.source_table)
        if source is None:
            run.blocking.append(f"The source table {table.source_table} is not in the source any more")
            continue
        report, _ = dry_run_table(table, source, sample_rows)
        run.tables.append(report)
        for r in report.rules:
            by_rule[(table.target_table, r.target_field)] = r
            if r.failure_rate >= BLOCK_FAILURE_RATE:
                run.blocking.append(f"{table.target_table}.{r.target_field}: {r.failure_rate:.0%} of the sampled values fail, the rule is probably wrong")
            elif r.failure_rate >= WARN_FAILURE_RATE:
                run.warnings.append(f"{table.target_table}.{r.target_field}: {r.failure_rate:.0%} of the sampled values fail")
        if report.sample_rows and report.would_load == 0:
            run.blocking.append(f"{table.target_table}: none of the {report.sample_rows} sampled rows could be loaded")
    return run, by_rule
