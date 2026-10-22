import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.core.security import Principal
from app.llm.store import DbStore
from app.db.models import DiscoveryEvidence, Job, Source
from app.db.models.enums import JobStatus, Role, SourceKind
from app.db.repositories.jobs import JobRepository
from app.db.session import make_session_factory
from app.discovery import service
from app.llm.adapters import layout_resolver
from app.llm.factory import context_for, get_router, llm_available
from app.discovery.rea_adapter import ReaError
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1", tags=["discovery"])


class DiscoverRequest(BaseModel):
    data_source_id: uuid.UUID | None = None


class DiscoverResponse(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    kind: str
    confidence: float | None
    validated: bool | None = None
    method: str | None = None
    evidence_count: int


class EvidenceOut(BaseModel):
    id: uuid.UUID
    subject: str
    command: str
    rea_version: str | None
    duration_ms: int | None
    raw_json: dict[str, Any]


@router.post("/sources/{source_id}/discover", response_model=DiscoverResponse, status_code=status.HTTP_201_CREATED)
def discover(
    source_id: uuid.UUID,
    request: Request,
    body: DiscoverRequest | None = None,
    principal: Principal = Depends(require_role(Role.ANALYST)),
    db: Session = Depends(get_tenant_db),
) -> DiscoverResponse:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    data_source_id = body.data_source_id if body else None
    if data_source_id is not None:
        data_source = db.get(Source, data_source_id)
        if data_source is None or source.kind is not SourceKind.DOTNET_BINARY or data_source.kind is not SourceKind.DOTNET_BINARY:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A data file can only be attached to a .NET source")
    if source.kind is SourceKind.CSV:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Delimited text files are profiled directly")

    job = JobRepository(db).add(Job(source_id=source.id, data_source_id=data_source_id, created_by=principal.user_id, status=JobStatus.CREATED,
                          token_budget=DbStore(principal.org_id, make_session_factory()).org_settings().job_token_budget))
    db.commit()  # the router logs and charges tokens in its own transactions, so the job must be visible to them
    try:
        batch = layout_resolver(get_router(), context_for(principal.org_id, job.id, make_session_factory())) if llm_available() else None
        outcome = service.run_discovery(db, job, batch_resolver=batch)
    except ReaError as exc:
        db.commit()  # keep the FAILED job and its event
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Analysis failed: {exc.kind}") from None
    record_audit(
        db, AuditAction.DISCOVERY, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id),
        ip=client_ip(request), detail={"kind": outcome.kind, "evidence": outcome.evidence_count},
    )  # fmt: skip
    return DiscoverResponse(
        job_id=job.id, status=job.status, kind=outcome.kind, confidence=outcome.confidence,
        validated=outcome.layout.validated if outcome.layout else None,
        method=outcome.layout.method if outcome.layout else "sqlite", evidence_count=outcome.evidence_count,
    )  # fmt: skip


@router.get("/jobs/{job_id}/discovery")
def get_discovery(job_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    result = service.load_result(db, job_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No discovery result for this job")
    return result


@router.get("/jobs/{job_id}/discovery/evidence", response_model=list[EvidenceOut])
def get_evidence(
    job_id: uuid.UUID,
    subject: str = Query(..., min_length=1, max_length=300, description="A field or column such as PATIENT.LastNm"),
    _: Principal = Depends(get_principal),
    db: Session = Depends(get_tenant_db),
) -> list[DiscoveryEvidence]:
    if subject.startswith("command:"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Raw command output is not served here")
    rows = list(db.scalars(select(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job_id, DiscoveryEvidence.subject == subject)))
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No evidence for this field")
    return rows
