import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.core.security import Principal
from app.db.models import Job, Source
from app.db.models.enums import JobStatus, Role, SourceKind
from app.db.repositories.jobs import JobRepository
from app.profiling import service
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1", tags=["profiling"])


class ProfileSummary(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    tables: int
    rows: int
    pii_columns: int
    quality_score: float
    issue_counts: dict[str, int]


def _run(db: Session, job: Job, principal: Principal, request: Request) -> ProfileSummary:
    try:
        outcome = service.run_profiling(db, job)
    except service.ProfilingUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except Exception:
        db.commit()  # keep the FAILED state and its event
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Profiling failed") from None
    record_audit(
        db, AuditAction.PROFILE, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id),
        ip=client_ip(request), detail={"rows": outcome.profile.rows_total, "quality_score": outcome.quality.score},
    )  # fmt: skip
    return ProfileSummary(
        job_id=job.id, status=job.status, tables=len(outcome.profile.tables), rows=outcome.profile.rows_total,
        pii_columns=sum(1 for t in outcome.profile.tables for c in t.columns if c.pii),
        quality_score=outcome.quality.score, issue_counts=outcome.quality.issue_counts,
    )  # fmt: skip


@router.post("/jobs/{job_id}/profile", response_model=ProfileSummary)
def profile_job(
    job_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)
) -> ProfileSummary:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _run(db, job, principal, request)


@router.post("/sources/{source_id}/profile", response_model=ProfileSummary, status_code=status.HTTP_201_CREATED)
def profile_text_source(
    source_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)
) -> ProfileSummary:
    """Delimited text has no structure to discover, so it is profiled directly in a new job."""
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if source.kind is not SourceKind.CSV:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Run discovery for databases and binary files first")
    job = JobRepository(db).add(Job(source_id=source.id, created_by=principal.user_id, status=JobStatus.CREATED))
    return _run(db, job, principal, request)


@router.get("/jobs/{job_id}/profile")
def get_profile(job_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    result = service.load_profile(db, job_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This job has not been profiled")
    return result


@router.get("/jobs/{job_id}/quality")
def get_quality(
    job_id: uuid.UUID,
    issue_type: str | None = Query(None, alias="type"),
    table: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _: Principal = Depends(get_principal),
    db: Session = Depends(get_tenant_db),
) -> dict[str, Any]:
    report = service.load_quality(db, job_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This job has not been profiled")
    issues = [i for i in report["issues"] if (issue_type is None or i["type"] == issue_type) and (table is None or i["table"] == table)]
    return {
        "score": report["score"], "formula": report["formula"], "tables": report["tables"], "issue_counts": report["issue_counts"],
        "issues_total": len(issues), "issues_truncated": report["issues_truncated"], "issues": issues[offset : offset + limit],
    }  # fmt: skip
