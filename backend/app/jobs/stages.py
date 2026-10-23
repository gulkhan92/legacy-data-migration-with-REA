"""The stages of a migration and the code that runs one of them for a job.

A stage runner does not know about Celery. It is called with a job id, the organization and the user the job
belongs to, checks whether there is anything to do, does it, and returns a result. That keeps every rule here
testable without a broker, and it is what makes the stages safe to run twice:

- a stage that is already behind the job is skipped, so a redelivered or duplicated task does nothing;
- a stage that cannot run yet (rules still need a code map, no target schema chosen) is reported as blocked and the
  job stays where it is, waiting for a person;
- a transient failure (database restart, network) is raised as TransientStageError and the task is retried with
  backoff, nothing is marked failed yet;
- any other failure rolls the stage back and moves the job to FAILED with an event that names the stage;
- one stage per job runs at a time, guarded by a PostgreSQL advisory lock held on its own connection.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.db.job_state import InvalidTransition
from app.db.models import Job, JobEvent, Source, TargetSchema
from app.db.models.enums import JobStatus, SourceKind
from app.db.repositories.jobs import TERMINAL_STATUSES, JobRepository
from app.db.session import make_session_factory, scoped_session
from app.services.audit import AuditAction, record_audit

log = logging.getLogger(__name__)


class Stage(StrEnum):
    DISCOVER = "discover"
    PROFILE = "profile"
    MAP = "map"
    RULES = "rules"
    DRY_RUN = "dry_run"
    LOAD = "load"


STAGE_ORDER = list(Stage)
# What the run endpoint does when no stages are named: everything that needs no human decision.
PIPELINE_TO_REVIEW = [Stage.DISCOVER, Stage.PROFILE, Stage.MAP, Stage.RULES]


class Outcome(StrEnum):
    DONE = "done"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class StageResult:
    stage: str
    outcome: str
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TransientStageError(Exception):
    """A failure that is likely to go away: the stage was rolled back and the task should be retried."""


class StageBusy(Exception):
    """Another stage of the same job is running right now."""


class Finished(Exception):
    """A stage that ended by itself with a result, after leaving the job in the state it should stay in."""

    def __init__(self, result: "StageResult") -> None:
        super().__init__(result.message)
        self.result = result


class Blocked(Exception):
    """The stage cannot run yet and a person has to do something first."""


TRANSIENT = (OperationalError, InterfaceError, ConnectionError, TimeoutError)

_RANK = {
    JobStatus.CREATED: 0, JobStatus.UPLOADED: 1, JobStatus.DISCOVERING: 2, JobStatus.PROFILED: 3, JobStatus.MAPPED: 4,
    JobStatus.AWAITING_REVIEW: 5, JobStatus.DRY_RUN: 6, JobStatus.READY: 7, JobStatus.LOADING: 8, JobStatus.RECONCILING: 9, JobStatus.COMPLETED: 10,
}  # fmt: skip


@dataclass(frozen=True)
class Context:
    org_id: uuid.UUID
    user_id: uuid.UUID | None
    params: dict[str, Any]


# ------------------------------------------------------------------ whether a stage has anything to do


def skip_reason(stage: Stage, job: Job, source: Source | None) -> str | None:
    if job.status in (JobStatus.FAILED, JobStatus.ROLLED_BACK):
        return f"The job is {job.status.value.lower().replace('_', ' ')}"
    rank = _RANK[job.status]
    if stage is Stage.DISCOVER:
        if source is not None and source.kind is SourceKind.CSV:
            return "Delimited text has no structure to discover, it is profiled directly"
        return "Already discovered" if rank >= _RANK[JobStatus.PROFILED] else None
    if stage is Stage.PROFILE:
        return "Already profiled" if rank >= _RANK[JobStatus.PROFILED] else None
    if stage is Stage.MAP:
        return "Already mapped" if rank >= _RANK[JobStatus.MAPPED] else None
    if stage is Stage.RULES:
        return "Rules already generated" if rank >= _RANK[JobStatus.AWAITING_REVIEW] else None
    if stage is Stage.DRY_RUN:
        return "The job is past the dry run" if rank >= _RANK[JobStatus.LOADING] else None
    if stage is Stage.LOAD:
        return "Already loaded" if job.status is JobStatus.COMPLETED else None
    return None


# ------------------------------------------------------------------ the work of each stage


def _discover(db: Session, job: Job, ctx: Context) -> dict[str, Any]:
    from app.discovery import service
    from app.llm.adapters import layout_resolver
    from app.llm.factory import context_for, get_router, llm_available

    batch = layout_resolver(get_router(), context_for(ctx.org_id, job.id, make_session_factory())) if llm_available() else None
    outcome = service.run_discovery(db, job, batch_resolver=batch)
    record_audit(db, AuditAction.DISCOVERY, org_id=ctx.org_id, user_id=ctx.user_id, entity_type="job", entity_id=str(job.id), detail={"kind": outcome.kind, "evidence": outcome.evidence_count})
    return {"kind": outcome.kind, "evidence": outcome.evidence_count}


def _profile(db: Session, job: Job, ctx: Context) -> dict[str, Any]:
    from app.profiling import service

    try:
        outcome = service.run_profiling(db, job)
    except service.ProfilingUnavailable as exc:
        raise Blocked(str(exc)) from None
    record_audit(db, AuditAction.PROFILE, org_id=ctx.org_id, user_id=ctx.user_id, entity_type="job", entity_id=str(job.id), detail={"rows": outcome.profile.rows_total, "quality_score": outcome.quality.score})
    return {"rows": outcome.profile.rows_total, "tables": len(outcome.profile.tables), "quality_score": outcome.quality.score}


def _target_schema_name(db: Session, job: Job, ctx: Context) -> str | None:
    named = ctx.params.get("target_schema")
    if named:
        return str(named)
    schema = db.get(TargetSchema, job.target_schema_id) if job.target_schema_id else None
    return schema.name if schema else None


def _map(db: Session, job: Job, ctx: Context) -> dict[str, Any]:
    from app.llm.adapters import mapping_resolver
    from app.llm.factory import context_for, get_router, llm_available
    from app.mapping import service
    from app.mapping.embedding import EmbeddingUnavailable

    name = _target_schema_name(db, job, ctx)
    if not name:
        raise Blocked("No target schema was chosen for this job")
    try:
        resolver = mapping_resolver(get_router(), context_for(ctx.org_id, job.id, make_session_factory())) if llm_available() else None
        outcome = service.run_mapping(db, job, name, resolver=resolver)
    except service.MappingUnavailable as exc:
        raise Blocked(str(exc)) from None
    except EmbeddingUnavailable:
        raise RuntimeError("The embedding model is not available") from None
    record_audit(db, AuditAction.MAPPING_RUN, org_id=ctx.org_id, user_id=ctx.user_id, entity_type="job", entity_id=str(job.id), detail={"schema": name, "counts": outcome.counts})
    return {"schema": name, "columns": len(outcome.decisions), "counts": outcome.counts, "llm_requests": outcome.llm_requests}


def _rules(db: Session, job: Job, ctx: Context) -> dict[str, Any]:
    from app.mapping import service as mapping_service
    from app.transform import service

    if ctx.user_id is None:
        raise Blocked("Approving mappings needs a user")
    approved = mapping_service.approve_decided(db, job, ctx.user_id)
    try:
        result = service.generate(db, job)
    except service.TransformUnavailable as exc:
        raise Blocked(str(exc)) from None
    record_audit(db, AuditAction.MAPPING_APPROVE, org_id=ctx.org_id, user_id=ctx.user_id, entity_type="job", entity_id=str(job.id), detail={"approved": approved, "mode": "pipeline"})
    return {"approved_mappings": approved, "rules_created": result.created, "needs_input": result.needs_input}


def _dry_run(db: Session, job: Job, ctx: Context) -> dict[str, Any]:
    from app.transform import service

    try:
        run = service.dry_run(db, job)
    except service.TransformUnavailable as exc:
        raise Blocked(str(exc)) from None
    if not run.ready:
        # The state moves back to review so a person can fix what blocks the load; the reasons are in the event.
        raise Blocked("The dry run found problems: " + "; ".join(run.blocking[:3]))
    return {"ready": True, "warnings": len(run.warnings)}


def _load(db: Session, job: Job, ctx: Context) -> dict[str, Any]:
    from app.transform import service

    try:
        outcome = service.load_job(db, job)
    except service.LoadBlocked as exc:
        raise Blocked(str(exc)) from None
    except service.LoadFailed as exc:
        # The load service stored the failed report and moved the job to FAILED; keep both.
        db.commit()
        raise Finished(StageResult(Stage.LOAD.value, Outcome.FAILED, "The load failed and was rolled back", {"failed_checks": (exc.report or {}).get("failed_checks", [])})) from None
    record_audit(db, AuditAction.JOB_LOAD, org_id=ctx.org_id, user_id=ctx.user_id, entity_type="job", entity_id=str(job.id), detail={"loaded": outcome.loaded, "quarantined": outcome.quarantined})
    return {"loaded": outcome.loaded, "quarantined": outcome.quarantined, "quality_before": outcome.quality_before, "quality_after": outcome.quality_after}


HANDLERS: dict[Stage, Callable[[Session, Job, Context], dict[str, Any]]] = {
    Stage.DISCOVER: _discover, Stage.PROFILE: _profile, Stage.MAP: _map, Stage.RULES: _rules, Stage.DRY_RUN: _dry_run, Stage.LOAD: _load,
}  # fmt: skip


# ------------------------------------------------------------------ events and failure


def note(org_id: uuid.UUID, user_id: uuid.UUID | None, job_id: uuid.UUID, message: str, detail: dict[str, Any] | None = None) -> None:
    """A progress event that does not change the state: it shows what a stage is doing. Written in its own transaction."""
    with scoped_session(org_id=org_id, user_id=user_id) as db:
        status = db.scalar(select(Job.status).where(Job.id == job_id))
        if status is not None:
            db.add(JobEvent(org_id=org_id, job_id=job_id, from_status=status, to_status=status, message=message, detail=detail or {}))


def fail_job(org_id: uuid.UUID, user_id: uuid.UUID | None, job_id: uuid.UUID, stage: str, message: str, detail: dict[str, Any] | None = None) -> bool:
    """Move a job that is not finished to FAILED. Returns False when it was already finished."""
    with scoped_session(org_id=org_id, user_id=user_id) as db:
        job = db.get(Job, job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return False
        try:
            JobRepository(db).transition(job, JobStatus.FAILED, message=message, detail={"stage": stage, **(detail or {})})
        except InvalidTransition:
            return False
        return True


# ------------------------------------------------------------------ one job, one stage at a time


class JobLock:
    """A PostgreSQL advisory lock for a job, held on a connection of its own for as long as the stage runs."""

    def __init__(self, job_id: uuid.UUID) -> None:
        self.key = int.from_bytes(job_id.bytes[:8], "big", signed=True)
        self.connection = None

    def acquire(self) -> bool:
        connection = db_session.get_engine().connect().execution_options(isolation_level="AUTOCOMMIT")
        if connection.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}).scalar():
            self.connection = connection
            return True
        connection.close()
        return False

    def release(self) -> None:
        if self.connection is not None:
            try:
                self.connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
            finally:
                self.connection.close()
                self.connection = None


def run_stage(stage: Stage, job_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID | None, params: dict[str, Any] | None = None) -> StageResult:
    """Run one stage of a job. Raises StageBusy and TransientStageError, which the task turns into retries."""
    lock = JobLock(job_id)
    if not lock.acquire():
        raise StageBusy(f"Another stage of job {job_id} is running")
    try:
        return _run_locked(stage, job_id, Context(org_id, user_id, params or {}))
    finally:
        lock.release()


def _run_locked(stage: Stage, job_id: uuid.UUID, ctx: Context) -> StageResult:
    detail: dict[str, Any] = {}
    try:
        with scoped_session(org_id=ctx.org_id, user_id=ctx.user_id) as db:
            job = db.get(Job, job_id)
            if job is None:
                return StageResult(stage.value, Outcome.SKIPPED, "The job does not exist")
            reason = skip_reason(stage, job, db.get(Source, job.source_id))
            if reason:
                return StageResult(stage.value, Outcome.SKIPPED, reason)
            note(ctx.org_id, ctx.user_id, job_id, f"Stage {stage.value} started")
            try:
                detail = HANDLERS[stage](db, job, ctx)
            except Blocked as exc:
                # What the stage moved (a dry run goes back to review) stays, so the job shows where it waits.
                db.commit()
                result = StageResult(stage.value, Outcome.BLOCKED, str(exc))
            except Finished as done:
                result = done.result
            except InvalidTransition as exc:
                db.rollback()
                return StageResult(stage.value, Outcome.SKIPPED, f"The job changed while the stage ran ({exc})")
            else:
                result = StageResult(stage.value, Outcome.DONE, "", detail)
    except Exception as exc:
        return _failed(stage, ctx, job_id, exc)
    note(ctx.org_id, ctx.user_id, job_id, f"Stage {stage.value} {'finished' if result.outcome == Outcome.DONE else 'is waiting'}" + (f": {result.message}" if result.message else ""),
         {"stage": stage.value, "outcome": result.outcome, **result.detail})  # fmt: skip
    return result


def _failed(stage: Stage, ctx: Context, job_id: uuid.UUID, exc: Exception) -> StageResult:
    if isinstance(exc, TRANSIENT):
        log.warning("stage %s of job %s hit a transient error: %s", stage.value, job_id, type(exc).__name__)
        raise TransientStageError(type(exc).__name__) from exc
    log.exception("stage %s of job %s failed", stage.value, job_id)
    fail_job(ctx.org_id, ctx.user_id, job_id, stage.value, f"Stage {stage.value} failed ({type(exc).__name__})", {"error": type(exc).__name__})
    return StageResult(stage.value, Outcome.FAILED, f"Stage {stage.value} failed ({type(exc).__name__})")
