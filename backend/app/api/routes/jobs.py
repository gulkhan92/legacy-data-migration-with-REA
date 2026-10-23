"""Jobs: create, list, inspect, run stages in the background, cancel, and follow progress.

Stages run in Celery workers (Redis broker). The run and stage endpoints answer 202 at once with the task id; the
progress is in the job events, which can be read as a list or followed as a Server-Sent Events stream.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.api.pagination import Page, PageParams
from app.core.config import get_settings
from app.core.security import Principal
from app.db.job_state import InvalidTransition
from app.db.models import Job, JobEvent, Source, TargetSchema
from app.db.models.enums import JobStatus, Role, SourceKind
from app.db.repositories.jobs import TERMINAL_STATUSES, JobRepository
from app.db.session import make_session_factory, scoped_session
from app.jobs.dispatch import QueueUnavailable, enqueue
from app.jobs.stages import PIPELINE_TO_REVIEW, STAGE_ORDER, Stage
from app.llm.store import DbStore
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])

NEXT_STAGE = {
    JobStatus.CREATED: Stage.DISCOVER, JobStatus.PROFILED: Stage.MAP, JobStatus.MAPPED: Stage.RULES, JobStatus.AWAITING_REVIEW: Stage.DRY_RUN, JobStatus.READY: Stage.LOAD,
}  # fmt: skip


class JobCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"source_id": "5b0f2a9e-6f0e-4a3e-9d57-2c1f6b8d3a10", "target_schema": "commerce"}]})

    source_id: uuid.UUID
    data_source_id: uuid.UUID | None = Field(None, description="The data file of a .NET source whose assembly is source_id")
    target_schema: str | None = Field(None, min_length=1, max_length=100, description="Name of a target schema, for example commerce or clinic")


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: JobStatus
    source_id: uuid.UUID
    data_source_id: uuid.UUID | None
    target_schema_id: uuid.UUID | None
    error_message: str | None
    token_budget: int
    tokens_used: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    next_stage: Stage | None = Field(None, description="The stage that moves this job forward, when one is available")


class RunRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"stages": ["discover", "profile", "map", "rules"], "target_schema": "commerce"}]})

    stages: list[Stage] | None = Field(None, description="Stages to run in order. Default: everything up to the review of the rules")
    target_schema: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("stages")
    @classmethod
    def _stages(cls, value: list[Stage] | None) -> list[Stage] | None:
        if value is None:
            return None
        if not value or len(set(value)) != len(value):
            raise ValueError("name each stage once")
        return sorted(value, key=STAGE_ORDER.index)


class StageRequest(BaseModel):
    target_schema: str | None = Field(None, min_length=1, max_length=100)


class Queued(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"job_id": "0c9f2f5c-3b1d-4c43-8a54-0f0e8c1d7a77", "stages": ["profile"], "task_id": "d1c7a7e0-4b73-4a0b-8c53-6c3a0f6a1f11", "execution": "celery"}]})

    job_id: uuid.UUID
    stages: list[Stage]
    task_id: str
    execution: str


class EventOut(BaseModel):
    id: int
    from_status: JobStatus | None
    to_status: JobStatus
    message: str | None
    detail: dict[str, Any]
    created_at: datetime


class EventList(BaseModel):
    items: list[EventOut]
    last_id: int | None = Field(description="Pass as `after` to get only newer events")


def _out(job: Job) -> JobOut:
    out = JobOut.model_validate(job)
    out.next_stage = NEXT_STAGE.get(job.status)
    return out


def _job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _schema_by_name(db: Session, name: str) -> TargetSchema:
    schema = db.scalar(select(TargetSchema).where(TargetSchema.name == name).order_by(TargetSchema.version.desc()))
    if schema is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown target schema: {name}")
    return schema


# ------------------------------------------------------------------ create, list, get


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED, summary="Create a job for an uploaded source")
def create_job(body: JobCreate, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> JobOut:
    source = db.get(Source, body.source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if body.data_source_id is not None:
        data = db.get(Source, body.data_source_id)
        if data is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
        if source.kind is not SourceKind.DOTNET_BINARY or data.kind is not SourceKind.DOTNET_BINARY:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A data file can only be attached to a .NET source")
    schema = _schema_by_name(db, body.target_schema) if body.target_schema else None
    budget = DbStore(principal.org_id, make_session_factory()).org_settings().job_token_budget
    job = JobRepository(db).add(Job(source_id=source.id, data_source_id=body.data_source_id, target_schema_id=schema.id if schema else None, created_by=principal.user_id,
                                    status=JobStatus.CREATED, token_budget=budget))  # fmt: skip
    record_audit(db, AuditAction.JOB_CREATE, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request), detail={"source_id": str(source.id)})
    return _out(job)


@router.get("", response_model=Page[JobOut], summary="List jobs, newest first")
def list_jobs(
    status_filter: JobStatus | None = Query(None, alias="status"), source_id: uuid.UUID | None = None, page: PageParams = Depends(),
    _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db),
) -> Page[JobOut]:
    stmt = select(Job)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)
    if source_id:
        stmt = stmt.where(Job.source_id == source_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Job.created_at.desc(), Job.id).limit(page.limit).offset(page.offset)).all()
    return Page[JobOut](items=[_out(j) for j in rows], total=int(total), limit=page.limit, offset=page.offset)


@router.get("/{job_id}", response_model=JobOut, summary="One job")
def get_job(job_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> JobOut:
    return _out(_job(db, job_id))


# ------------------------------------------------------------------ run stages in the background


def _check_runnable(db: Session, job: Job, stages: list[Stage], target_schema: str | None) -> None:
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"A job in state {job.status.value} cannot run more stages")
    if target_schema:
        _schema_by_name(db, target_schema)
    elif Stage.MAP in stages and job.target_schema_id is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="target_schema is required to map: the job has none yet")


def _queue(job: Job, stages: list[Stage], principal: Principal, request: Request, db: Session, target_schema: str | None) -> Queued:
    record_audit(db, AuditAction.JOB_RUN, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request), detail={"stages": [s.value for s in stages]})
    db.commit()  # the worker must see the job, and the audit entry stays even when the queue is down
    try:
        queued = enqueue(stages, job.id, principal.org_id, principal.user_id, {"target_schema": target_schema} if target_schema else {})
    except QueueUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The job queue is not available, try again shortly") from None
    return Queued(job_id=job.id, stages=stages, **queued)


@router.post("/{job_id}/run", response_model=Queued, status_code=status.HTTP_202_ACCEPTED, summary="Run the pipeline up to the review of the rules")
def run_pipeline(job_id: uuid.UUID, request: Request, body: RunRequest | None = None, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> Queued:
    body = body or RunRequest()
    job = _job(db, job_id)
    stages = body.stages or list(PIPELINE_TO_REVIEW)
    _check_runnable(db, job, stages, body.target_schema)
    return _queue(job, stages, principal, request, db, body.target_schema)


@router.post("/{job_id}/stages/{stage}", response_model=Queued, status_code=status.HTTP_202_ACCEPTED, summary="Run one stage in the background")
def run_one_stage(
    job_id: uuid.UUID, stage: Stage, request: Request, body: StageRequest | None = None, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)
) -> Queued:
    job = _job(db, job_id)
    target = body.target_schema if body else None
    _check_runnable(db, job, [stage], target)
    return _queue(job, [stage], principal, request, db, target)


# ------------------------------------------------------------------ cancel


@router.post("/{job_id}/cancel", response_model=JobOut, summary="Stop a job that is not finished")
def cancel_job(job_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> JobOut:
    """Marks the job as failed with the reason "cancelled". Stages that have not started will skip it. A stage that is running
    right now holds the job and is not interrupted: the call answers 409 and can be repeated when the stage has finished."""
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    try:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update(nowait=True))
    except OperationalError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A stage is running right now; cancel again when it has finished") from None
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"The job is already {job.status.value.lower().replace('_', ' ')}")
    try:
        JobRepository(db).transition(job, JobStatus.FAILED, message="Cancelled by a user", detail={"cancelled": True, "by": str(principal.user_id)})
    except InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    record_audit(db, AuditAction.JOB_CANCEL, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request))
    return _out(job)


# ------------------------------------------------------------------ events


def _event_out(e: JobEvent) -> EventOut:
    return EventOut(id=e.id, from_status=e.from_status, to_status=e.to_status, message=e.message, detail=e.detail, created_at=e.created_at)


@router.get("/{job_id}/events", response_model=EventList, summary="The events of a job, oldest first")
def list_events(
    job_id: uuid.UUID, after: int = Query(0, ge=0, description="Only events with a larger id"), limit: int = Query(200, ge=1, le=1000),
    _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db),
) -> EventList:
    _job(db, job_id)
    rows = db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after).order_by(JobEvent.id).limit(limit)).all()
    return EventList(items=[_event_out(e) for e in rows], last_id=rows[-1].id if rows else None)


def _sse(event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _poll(org_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID, cursor: int) -> tuple[JobStatus | None, list[dict[str, Any]]]:
    """The job state first, then the events after the cursor: when the state is final every event up to it is in the list."""
    with scoped_session(org_id=org_id, user_id=user_id) as db:
        state = db.scalar(select(Job.status).where(Job.id == job_id))
        rows = db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > cursor).order_by(JobEvent.id).limit(500)).all()
        return state, [_event_out(e).model_dump(mode="json") for e in rows]


async def _stream(request: Request, principal: Principal, job_id: uuid.UUID, cursor: int) -> AsyncIterator[str]:
    settings = get_settings()
    started = last_beat = time.monotonic()
    yield "retry: 3000\n\n"
    while True:
        if await request.is_disconnected():
            return
        state, events = await run_in_threadpool(_poll, principal.org_id, principal.user_id, job_id, cursor)
        for e in events:
            cursor = e["id"]
            yield _sse("job", e, e["id"])
        if state is None:
            return
        if state in TERMINAL_STATUSES and len(events) < 500:
            yield _sse("end", {"status": state.value, "last_id": cursor})
            return
        now = time.monotonic()
        if now - started >= settings.sse_max_seconds:
            yield _sse("timeout", {"status": state.value, "last_id": cursor})
            return
        if events:
            last_beat = now
        elif now - last_beat >= settings.sse_heartbeat_seconds:
            last_beat = now
            yield ": keepalive\n\n"
        await asyncio.sleep(settings.sse_poll_seconds)


@router.get("/{job_id}/events/stream", summary="Follow a job as Server-Sent Events", response_class=StreamingResponse, responses={200: {"content": {"text/event-stream": {}}}})
async def stream_events(
    job_id: uuid.UUID, request: Request, after: int | None = Query(None, ge=0, description="Resume after this event id (the Last-Event-ID header works too)"),
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    """One `job` event per entry of the job's history, then new ones as they happen, and an `end` event when the job has finished.
    The stream closes by itself after a few minutes; a browser EventSource reconnects and resumes from the last event it received."""
    header = request.headers.get("last-event-id", "")
    cursor = after if after is not None else (int(header) if header.isdigit() else 0)
    exists = await run_in_threadpool(lambda: _poll(principal.org_id, principal.user_id, job_id, 2**62)[0] is not None)
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return StreamingResponse(_stream(request, principal, job_id, cursor), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
