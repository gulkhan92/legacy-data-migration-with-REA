"""Orchestration of the transform, load and reconciliation steps of a job, and the job state changes that go with them.

    MAPPED -> AWAITING_REVIEW     rules are generated from the approved mappings
    AWAITING_REVIEW -> DRY_RUN    a dry run on a sample
    DRY_RUN -> READY              nothing blocks a load (or back to AWAITING_REVIEW when something does)
    READY -> LOADING -> RECONCILING -> COMPLETED      the load, in one transaction
    COMPLETED or FAILED -> ROLLED_BACK                everything the job put in the warehouse is removed
"""

import time
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.billing import metering
from app.db.job_state import InvalidTransition
from app.db.models import Job, QuarantinedRow, ReconciliationReport, Source, TransformRule
from app.db.models.enums import JobStatus
from app.db.repositories.jobs import JobRepository
from app.db.repositories.warehouse import WarehouseRepository
from app.profiling import service as profiling_service
from app.profiling.readers import DEFAULT_CHUNK, TableSource
from app.transform import dry_run as dry
from app.transform import load, reconcile, rules
from app.transform.dsl import RuleError, apply_steps, parse_steps
from app.transform.engine import to_text
from app.transform.load import StageAggregates
from app.transform.plan import Plan, build_plan, warehouse_table

BEFORE_LOAD = (JobStatus.MAPPED, JobStatus.AWAITING_REVIEW, JobStatus.DRY_RUN, JobStatus.READY)


class TransformUnavailable(Exception):
    """The job is not in a state where this step can run."""


class LoadBlocked(TransformUnavailable):
    def __init__(self, message: str, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class LoadFailed(Exception):
    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class LoadOutcome:
    job_id: uuid.UUID
    tables: list[dict[str, Any]]
    loaded: int
    quarantined: int
    passed: bool
    quality_before: float | None
    quality_after: float | None
    seconds: float
    report: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers


def _move(jobs: JobRepository, job: Job, target: JobStatus, message: str, detail: dict[str, Any] | None = None) -> None:
    jobs.transition(job, target, message=message, detail=detail)


def _source(session: Session, job: Job) -> Source:
    source = session.get(Source, job.source_id)
    if source is None:
        raise TransformUnavailable("The job's source does not exist")
    return source


def _plan(session: Session, job: Job) -> Plan:
    if job.target_schema_id is None:
        raise TransformUnavailable("The job has no target schema; map it first")
    return build_plan(session, job.id, job.target_schema_id)


def _sources(session: Session, job: Job, stack: ExitStack) -> dict[str, TableSource]:
    return {t.name: t for t in profiling_service._tables_for(session, job, _source(session, job), stack)}


# ------------------------------------------------------------------ rules


def generate(session: Session, job: Job) -> rules.GenerationResult:
    if job.status not in BEFORE_LOAD:
        raise TransformUnavailable(f"Rules cannot be generated for a job in state {job.status.value}")
    if job.target_schema_id is None:
        raise TransformUnavailable("The job has no target schema; map it first")
    result = rules.generate_rules(session, job)
    jobs = JobRepository(session)
    if job.status is JobStatus.MAPPED:
        _move(jobs, job, JobStatus.AWAITING_REVIEW, "Rules generated, waiting for review", {"created": result.created, "needs_input": result.needs_input})
    elif job.status in (JobStatus.READY, JobStatus.DRY_RUN):
        _move(jobs, job, JobStatus.AWAITING_REVIEW, "Rules changed, a new dry run is needed")
    return result


def validate_rule_on_sample(session: Session, job: Job, rule: TransformRule, sample_rows: int = dry.SAMPLE_ROWS) -> float:
    """Run one rule on a sample of the source and return its failure rate. Used before a person's rule is accepted."""
    try:
        parsed = parse_steps(rule.steps, len(rule.inputs))
    except RuleError as exc:
        raise rules.RuleRejected(str(exc)) from None
    with ExitStack() as stack:
        source = _sources(session, job, stack).get(rule.source_table or "")
        if source is None:
            raise TransformUnavailable(f"The source table {rule.source_table} is not available")
        chunk = next(iter(source.chunks(sample_rows)))
        if any(i.startswith("@") for i in rule.inputs):
            return 0.0  # a derived rule reads other rules' output, the dry run covers it
        out = apply_steps(parsed, [chunk[c] for c in rule.inputs])
    present = max(1, int(out.values.notna().sum() + out.failed.sum()))
    return float(out.failed.sum()) / present


def preview_rule(session: Session, job: Job, rule: TransformRule, steps: list[dict[str, Any]] | None = None, rows: int = 10) -> dict[str, Any]:
    """What a rule (the saved one, or steps a person is trying out) does to a sample of the source. Nothing is saved."""
    candidate = rule.steps if steps is None else steps
    try:
        parsed = parse_steps(candidate, len(rule.inputs))
    except RuleError as exc:
        raise rules.RuleRejected(str(exc)) from None
    if any(i.startswith("@") for i in rule.inputs):
        raise TransformUnavailable("A derived rule reads the output of other rules; its effect shows in the dry run")
    with ExitStack() as stack:
        source = _sources(session, job, stack).get(rule.source_table or "")
        if source is None:
            raise TransformUnavailable(f"The source table {rule.source_table} is not available")
        sample = next(iter(source.chunks(dry.SAMPLE_ROWS)), None)
        if sample is None or sample.empty:
            return {"sampled": 0, "failed": 0, "failure_rate": 0.0, "steps": [], "rows": []}
        out = apply_steps(parsed, [sample[c] for c in rule.inputs])
    failed = [bool(f) for f in out.failed.tolist()]
    positions = list(range(len(sample)))
    chosen = [i for i in positions if not failed[i]][:rows] + [i for i in positions if failed[i]][:rows]
    items = [
        {"row": int(sample.index[i]), "before": {c: dry._json(sample[c].iloc[i]) for c in rule.inputs}, "after": to_text(out.values.iloc[i]), "failed": failed[i], "kind": out.kind.iloc[i]}
        for i in chosen
    ]
    present = max(1, int(out.values.notna().sum()) + sum(failed))
    return {"sampled": len(sample), "failed": sum(failed), "failure_rate": round(sum(failed) / present, 4), "steps": [step.spec.name for step in parsed], "rows": items}


def update_rule(session: Session, job: Job, rule: TransformRule, steps: list[dict[str, Any]]) -> TransformRule:
    if job.status not in BEFORE_LOAD:
        raise TransformUnavailable(f"Rules cannot be changed for a job in state {job.status.value}")
    previous = (list(rule.steps), rule.origin, rule.status, dict(rule.failures), list(rule.notes))
    rules.set_rule(session, job, rule, steps)
    rate = validate_rule_on_sample(session, job, rule)
    if rate >= dry.BLOCK_FAILURE_RATE:
        rule.steps, rule.origin, rule.status, rule.failures, rule.notes = previous
        session.flush()
        raise rules.RuleRejected(f"On a sample of the source {rate:.0%} of the values fail with these steps, so they were not saved")
    rule.sample_failure_count = None
    if job.status in (JobStatus.READY, JobStatus.DRY_RUN):
        _move(JobRepository(session), job, JobStatus.AWAITING_REVIEW, "A rule changed, a new dry run is needed")
    return rule


def set_code_map(session: Session, job: Job, rule: TransformRule, mapping: dict[str, str], passthrough: bool = False) -> TransformRule:
    return update_rule(session, job, rule, [{"op": "map_codes", "mapping": mapping, "passthrough": passthrough}])


# ------------------------------------------------------------------ dry run


def dry_run(session: Session, job: Job, sample_rows: int = dry.SAMPLE_ROWS) -> dry.DryRun:
    if job.status not in BEFORE_LOAD or job.status is JobStatus.MAPPED:
        raise TransformUnavailable("Generate and review the rules before a dry run" if job.status is JobStatus.MAPPED else f"A job in state {job.status.value} cannot be dry run")
    jobs = JobRepository(session)
    if job.status is JobStatus.READY:
        _move(jobs, job, JobStatus.AWAITING_REVIEW, "A new dry run was requested")
    if job.status is JobStatus.AWAITING_REVIEW:
        _move(jobs, job, JobStatus.DRY_RUN, "Dry run on a sample")
    plan = _plan(session, job)
    with ExitStack() as stack:
        run, by_rule = dry.dry_run(plan, _sources(session, job, stack), sample_rows)
    for rule in session.scalars(select(TransformRule).where(TransformRule.job_id == job.id)):
        report = by_rule.get((rule.target_table, rule.target_field))
        if report is not None:
            rule.failures = {"rows": report.rows, "nulls": report.nulls, "failures": report.failures, "out_of_range": report.out_of_range,
                             "step_failures": report.step_failures, "failure_rate": report.failure_rate}  # fmt: skip
            rule.sample_failure_count = report.failures + report.out_of_range
    if run.ready:
        _move(jobs, job, JobStatus.READY, "The dry run found nothing that blocks a load", {"warnings": len(run.warnings)})
    else:
        _move(jobs, job, JobStatus.AWAITING_REVIEW, "The dry run found problems that block a load", {"blocking": run.blocking[:10]})
    return run


# ------------------------------------------------------------------ load


def _existing_rows(session: Session, job: Job, plan: Plan) -> int:
    total = 0
    for table in plan.tables:
        total += session.scalar(text(f"SELECT count(*) FROM warehouse.{load.ident(table.target_table)} WHERE job_id = :job"), {"job": job.id}) or 0
    return int(total)


def load_job(session: Session, job: Job, *, chunk_size: int = DEFAULT_CHUNK) -> LoadOutcome:
    """Load the job in one transaction and reconcile it. Raises LoadBlocked when it cannot start and LoadFailed when it fails."""
    if job.status is not JobStatus.READY:
        raise LoadBlocked(f"A job in state {job.status.value} cannot be loaded; it must be READY (generate rules, review, dry run)")
    plan = _plan(session, job)
    if plan.issues or not plan.tables:
        raise LoadBlocked("The plan has problems that block a load", plan.issues or ["No rules"])
    if _existing_rows(session, job, plan):
        raise LoadBlocked("This job already has rows in the warehouse; roll it back first")

    jobs = JobRepository(session)
    started = time.perf_counter()
    _move(jobs, job, JobStatus.LOADING, "Loading into staging tables")
    session.flush()
    quality_before = _quality_before(session, job)
    moves: dict[str, load.MoveResult] = {}
    aggs: dict[str, StageAggregates] = {}
    report: dict[str, Any] | None = None
    try:
        with session.begin_nested():
            with ExitStack() as stack:
                sources = _sources(session, job, stack)
                for table in plan.tables:
                    load.create_staging(session, table)
                    aggs[table.target_table] = load.stage_table(session, table, sources[table.source_table], chunk_size)
                    moves[table.target_table] = load.validate_and_move(session, table, job.org_id, job.id)
            tables = [reconcile.reconcile_table(session, t, aggs[t.target_table], moves[t.target_table], job.id) for t in plan.tables]
            after = reconcile.quality_after(session, plan.tables, job.id)
            passed = all(not t.failed for t in tables)
            report = {
                "tables": [t.as_dict() for t in tables], "passed": passed,
                "quality": {"before": quality_before, "after": after["score"], "after_issue_counts": after["issue_counts"], "after_tables": after["tables"]},
                "failed_checks": [f"{t.target_table}.{c}" for t in tables for c in t.failed],
            }  # fmt: skip
            if not passed:
                raise LoadFailed("The reconciliation found differences: " + ", ".join(report["failed_checks"]), report)
    except LoadFailed as exc:
        session.refresh(job)
        _store_report(session, job, exc.report or {}, tables_loaded=0, quarantined=0, passed=False, before=quality_before, after=None)
        _move(jobs, job, JobStatus.FAILED, str(exc))
        raise
    except Exception as exc:
        session.refresh(job)
        _move(jobs, job, JobStatus.FAILED, f"The load failed and was rolled back: {type(exc).__name__}")
        raise LoadFailed(f"The load failed and was rolled back: {type(exc).__name__}: {exc}") from exc

    _move(jobs, job, JobStatus.RECONCILING, "Reconciling the loaded data")
    loaded = sum(m.loaded for m in moves.values())
    quarantined = sum(m.quarantined for m in moves.values())
    seconds = round(time.perf_counter() - started, 2)
    _store_report(session, job, report, loaded, quarantined, True, quality_before, report["quality"]["after"])
    _move(jobs, job, JobStatus.COMPLETED, "Load and reconciliation finished", {"loaded": loaded, "quarantined": quarantined, "seconds": seconds})
    metering.record_job_usage(session, job, loaded, int(seconds * 1000))
    return LoadOutcome(job.id, report["tables"], loaded, quarantined, True, quality_before, report["quality"]["after"], seconds, report)


def _quality_before(session: Session, job: Job) -> float | None:
    stored = profiling_service.load_quality(session, job.id)
    return float(stored["score"]) if stored else None


def _store_report(session: Session, job: Job, report: dict[str, Any], tables_loaded: int, quarantined: int, passed: bool, before: float | None, after: float | None) -> None:
    session.execute(delete(ReconciliationReport).where(ReconciliationReport.job_id == job.id))
    totals = report.get("tables", [])
    session.add(
        ReconciliationReport(
            org_id=job.org_id, job_id=job.id, source_rows=sum(t["source_rows"] for t in totals), loaded_rows=sum(t["loaded"] for t in totals) if passed else tables_loaded,
            quarantined_rows=sum(t["quarantined"] for t in totals) if passed else quarantined, quality_before=before, quality_after=after, passed=passed, checks=report,
        )  # fmt: skip
    )
    session.flush()


# ------------------------------------------------------------------ rollback and reads


def rollback_job(session: Session, job: Job) -> dict[str, Any]:
    """Remove everything the job loaded, by job id. The warehouse is back to the state before the load."""
    if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED):
        raise TransformUnavailable(f"A job in state {job.status.value} cannot be rolled back")
    removed = WarehouseRepository(session).delete_by_job(job.id)
    quarantined = session.execute(delete(QuarantinedRow).where(QuarantinedRow.job_id == job.id)).rowcount or 0
    session.execute(delete(ReconciliationReport).where(ReconciliationReport.job_id == job.id))
    metering.void_job_usage(session, job)
    _move(JobRepository(session), job, JobStatus.ROLLED_BACK, "Rolled back by job id", {"removed": removed, "quarantined_removed": quarantined})
    return {"removed": removed, "quarantined_removed": int(quarantined)}


def get_report(session: Session, job_id: uuid.UUID) -> ReconciliationReport | None:
    return session.scalar(select(ReconciliationReport).where(ReconciliationReport.job_id == job_id))


def quarantine_page(session: Session, job_id: uuid.UUID, reason: str | None, table: str | None, limit: int, offset: int) -> tuple[int, list[QuarantinedRow]]:
    stmt = select(QuarantinedRow).where(QuarantinedRow.job_id == job_id)
    if reason:
        stmt = stmt.where(QuarantinedRow.reason_code == reason)
    if table:
        stmt = stmt.where(QuarantinedRow.target_table == table)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(QuarantinedRow.target_table, QuarantinedRow.source_row_number).limit(limit).offset(offset)).all()
    return int(total), list(rows)


def warehouse_counts(session: Session, job_id: uuid.UUID) -> dict[str, int]:
    from app.db.base import Base

    counts = {}
    for name, table in Base.metadata.tables.items():
        if name.startswith("warehouse."):
            counts[table.name] = int(session.scalar(text(f"SELECT count(*) FROM {name} WHERE job_id = :job"), {"job": job_id}) or 0)
    return counts


__all__ = ["generate", "dry_run", "load_job", "rollback_job", "update_rule", "set_code_map", "get_report", "quarantine_page", "InvalidTransition", "warehouse_table"]
