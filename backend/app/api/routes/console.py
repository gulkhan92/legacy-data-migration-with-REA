"""Endpoints of the console that belong to the signed in organization: the overview, the notifications feed and the mapping memory.

Everything here is scoped to the caller's organization by Row Level Security like the rest of the API.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.api.pagination import Page, PageParams
from app.api.routes.jobs import JobOut, _enrich
from app.core.security import Principal
from app.db.models import Job, JobEvent, LlmCall, MappingMemory, Source, TargetField, TargetSchema, UsageEvent
from app.db.models.enums import JobStatus, LlmCallStatus, Role
from app.db.repositories.jobs import TERMINAL_STATUSES
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1", tags=["console"])


# ------------------------------------------------------------------ overview


def _month(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day.replace(day=1), time.min, tzinfo=UTC)
    end = datetime.combine((day.replace(day=28) + timedelta(days=4)).replace(day=1), time.min, tzinfo=UTC)
    return start, end


def _kpis(db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    """What this organization did in a period: finished jobs and how many succeeded, records loaded, how long loads took, tokens used."""
    finished = db.execute(
        select(func.count(), func.count().filter(Job.status == JobStatus.COMPLETED)).where(Job.status.in_([JobStatus.COMPLETED, JobStatus.FAILED]), Job.completed_at >= start, Job.completed_at < end)
    ).one()
    usage = db.execute(select(func.coalesce(func.sum(UsageEvent.records_loaded), 0), func.avg(UsageEvent.duration_ms)).where(UsageEvent.occurred_at >= start, UsageEvent.occurred_at < end)).one()
    tokens = db.scalar(
        select(func.coalesce(func.sum(LlmCall.tokens_in + LlmCall.tokens_out), 0)).where(LlmCall.status == LlmCallStatus.OK, LlmCall.cache_hit.is_(False), LlmCall.created_at >= start, LlmCall.created_at < end)
    )
    jobs, ok = int(finished[0]), int(finished[1])
    return {
        "jobs": jobs, "records": int(usage[0]), "success_rate": round(ok / jobs, 4) if jobs else None,
        "avg_duration_ms": round(float(usage[1])) if usage[1] is not None else None, "tokens": int(tokens or 0),
    }  # fmt: skip


@router.get("/overview", summary="This organization's month: KPIs, jobs in progress and recent jobs")
def overview(_: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    start, end = _month(today)
    prev_start, _ = _month((start - timedelta(days=1)).date())
    active = db.scalars(select(Job).where(Job.status.notin_(list(TERMINAL_STATUSES))).order_by(Job.updated_at.desc()).limit(6)).all()
    recent = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(8)).all()
    return {
        "month": {"from": start.date().isoformat(), "to": (end - timedelta(days=1)).date().isoformat()},
        "kpis": _kpis(db, start, end), "previous": _kpis(db, prev_start, start),
        "in_progress": [j.model_dump(mode="json") for j in _enrich(db, list(active))], "recent": [j.model_dump(mode="json") for j in _enrich(db, list(recent))],
        "total_jobs": db.scalar(select(func.count()).select_from(Job)) or 0,
    }  # fmt: skip


# ------------------------------------------------------------------ notifications

NOTABLE = [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ROLLED_BACK, JobStatus.AWAITING_REVIEW, JobStatus.READY]


class NotificationOut(BaseModel):
    id: int
    job_id: uuid.UUID
    source_name: str | None
    to_status: JobStatus
    message: str | None
    kind: str
    created_at: datetime


@router.get("/notifications", response_model=list[NotificationOut], summary="What happened lately to this organization's jobs")
def notifications(
    after: int = Query(0, ge=0, description="Only events with a larger id"), limit: int = Query(20, ge=1, le=100), _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")
) -> list[NotificationOut]:
    """A job that finished, failed, was rolled back or is waiting for a person, and a stage that is waiting for something (not the routine progress notes)."""
    changed = (JobEvent.from_status != JobEvent.to_status) & JobEvent.to_status.in_(NOTABLE)
    waiting = JobEvent.message.like("Stage % is waiting%")
    rows = db.execute(
        select(JobEvent, Source.name).join(Job, Job.id == JobEvent.job_id).join(Source, Source.id == Job.source_id)
        .where(or_(changed, waiting), JobEvent.id > after).order_by(JobEvent.id.desc()).limit(limit)
    ).all()  # fmt: skip
    out = []
    for event, name in rows:
        kind = "waiting" if event.from_status == event.to_status else {"COMPLETED": "success", "FAILED": "error", "ROLLED_BACK": "info"}.get(event.to_status.value, "action")
        out.append(NotificationOut(id=event.id, job_id=event.job_id, source_name=name, to_status=event.to_status, message=event.message, kind=kind, created_at=event.created_at))
    return out


# ------------------------------------------------------------------ mapping memory


class MemoryOut(BaseModel):
    id: uuid.UUID
    source_text: str
    target: str
    target_schema: str
    transform_hint: str | None
    times_approved: int
    last_approved_at: datetime


@router.get("/mapping-memory", response_model=Page[MemoryOut], summary="The mappings this organization has approved and that are reused on later jobs")
def list_memory(
    q: str | None = Query(None, max_length=100, description="Part of the source description or the target field"), schema: str | None = Query(None, max_length=100),
    page: PageParams = Depends(), _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function"),
) -> Page[MemoryOut]:
    stmt = select(MappingMemory, TargetField, TargetSchema).join(TargetField, TargetField.id == MappingMemory.target_field_id).join(TargetSchema, TargetSchema.id == TargetField.schema_id)
    if schema:
        stmt = stmt.where(TargetSchema.name == schema)
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(func.lower(MappingMemory.source_text).like(like) | func.lower(TargetField.name).like(like) | func.lower(TargetField.table_name).like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(MappingMemory.last_approved_at.desc(), MappingMemory.id).limit(page.limit).offset(page.offset)).all()
    items = [MemoryOut(id=m.id, source_text=m.source_text, target=f"{f.table_name}.{f.name}", target_schema=s.name, transform_hint=m.transform_hint, times_approved=m.times_approved, last_approved_at=m.last_approved_at)
             for m, f, s in rows]  # fmt: skip
    return Page[MemoryOut](items=items, total=int(total), limit=page.limit, offset=page.offset)


@router.delete("/mapping-memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Forget one remembered mapping")
def forget_memory(memory_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db, scope="function")) -> Response:
    memory = db.get(MappingMemory, memory_id)
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    db.delete(memory)
    record_audit(db, AuditAction.MEMORY_DELETE, org_id=principal.org_id, user_id=principal.user_id, entity_type="mapping_memory", entity_id=str(memory_id), ip=client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router", "JobOut"]
