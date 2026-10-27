import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.core.security import Principal
from app.db.models import Job, TargetField, TargetSchema
from app.db.models.enums import JobStatus, Role
from app.db.session import make_session_factory
from app.llm.adapters import mapping_resolver
from app.llm.factory import context_for, get_router, llm_available
from app.mapping import service
from app.mapping.embedding import EmbeddingUnavailable
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1", tags=["mapping"])


class MapRequest(BaseModel):
    target_schema: str = Field(min_length=1, max_length=100)


class MapSummary(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    target_schema: str
    columns: int
    counts: dict[str, int]
    llm_requests: int


@router.post("/jobs/{job_id}/map", response_model=MapSummary)
def map_job(
    job_id: uuid.UUID,
    body: MapRequest,
    request: Request,
    principal: Principal = Depends(require_role(Role.ANALYST)),
    db: Session = Depends(get_tenant_db, scope="function"),
) -> MapSummary:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    try:
        # The LLM only sees columns the rules could not settle; without a provider key they wait for review.
        resolver = mapping_resolver(get_router(), context_for(principal.org_id, job.id, make_session_factory())) if llm_available() else None
        outcome = service.run_mapping(db, job, body.target_schema, resolver=resolver)
    except service.MappingUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except EmbeddingUnavailable:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The embedding model is not available") from None
    record_audit(
        db, AuditAction.MAPPING_RUN, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id),
        ip=client_ip(request), detail={"schema": body.target_schema, "counts": outcome.counts},
    )  # fmt: skip
    return MapSummary(job_id=job.id, status=job.status, target_schema=body.target_schema, columns=len(outcome.decisions), counts=outcome.counts,
                      llm_requests=outcome.llm_requests)  # fmt: skip


@router.get("/jobs/{job_id}/mappings")
def list_mappings(job_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")) -> list[dict[str, Any]]:
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return service.mappings_for_job(db, job_id)


@router.get("/target-schemas")
def list_schemas(_: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")) -> list[dict[str, Any]]:
    rows = db.scalars(select(TargetSchema).order_by(TargetSchema.name, TargetSchema.version)).all()
    return [{"id": str(r.id), "name": r.name, "version": r.version, "description": r.description, "built_in": r.org_id is None} for r in rows]


@router.get("/target-schemas/{schema_id}/fields")
def schema_fields(schema_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db, scope="function")) -> list[dict[str, Any]]:
    if db.get(TargetSchema, schema_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target schema not found")
    fields = db.scalars(select(TargetField).where(TargetField.schema_id == schema_id).order_by(TargetField.table_name, TargetField.name)).all()
    return [
        {"id": str(f.id), "table": f.table_name, "name": f.name, "type": f.data_type, "required": f.required, "description": f.description,
         "synonyms": f.synonyms, "examples": f.examples, "pii": f.pii}
        for f in fields
    ]  # fmt: skip
