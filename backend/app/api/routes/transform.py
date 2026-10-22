import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.core.security import Principal
from app.db.models import Job, Mapping, TransformRule
from app.db.models.enums import JobStatus, Role
from app.mapping import service as mapping_service
from app.services.audit import AuditAction, record_audit
from app.transform import rules as rules_module
from app.transform import service

router = APIRouter(prefix="/api/v1/jobs/{job_id}", tags=["transform"])


def _job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _conflict(exc: service.TransformUnavailable) -> HTTPException:
    detail: Any = {"message": str(exc), "issues": exc.issues} if isinstance(exc, service.LoadBlocked) else str(exc)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _rule_out(r: TransformRule) -> dict[str, Any]:
    return {
        "id": str(r.id), "target_table": r.target_table, "target_field": r.target_field, "source_table": r.source_table, "inputs": r.inputs,
        "steps": r.steps, "status": r.status, "origin": r.origin, "notes": r.notes, "failures": r.failures, "sample_failure_count": r.sample_failure_count,
    }  # fmt: skip


class ApproveRequest(BaseModel):
    target_field_id: uuid.UUID | None = None


class StepsRequest(BaseModel):
    steps: list[dict[str, Any]] = Field(min_length=1, max_length=12)


class CodeMapRequest(BaseModel):
    mapping: dict[str, str] = Field(max_length=500)
    passthrough: bool = False


# ------------------------------------------------------------------ mapping approval (a person reviews, then the rules follow)


@router.post("/mappings/approve-decided")
def approve_decided(job_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, int]:
    job = _job(db, job_id)
    count = mapping_service.approve_decided(db, job, principal.user_id)
    record_audit(db, AuditAction.MAPPING_APPROVE, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request),
                 detail={"approved": count, "mode": "decided"})  # fmt: skip
    return {"approved": count}


@router.post("/mappings/{mapping_id}/approve")
def approve_one(
    job_id: uuid.UUID, mapping_id: uuid.UUID, body: ApproveRequest, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)
) -> dict[str, Any]:
    job = _job(db, job_id)
    mapping = db.scalar(select(Mapping).where(Mapping.id == mapping_id, Mapping.job_id == job.id))
    if mapping is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    mapping_service.approve_mapping(db, mapping, principal.user_id, target_field_id=body.target_field_id)
    record_audit(db, AuditAction.MAPPING_APPROVE, org_id=principal.org_id, user_id=principal.user_id, entity_type="mapping", entity_id=str(mapping.id), ip=client_ip(request),
                 detail={"override": body.target_field_id is not None})  # fmt: skip
    return {"id": str(mapping.id), "status": mapping.status.value, "method": mapping.method.value}


@router.post("/mappings/{mapping_id}/reject")
def reject_one(job_id: uuid.UUID, mapping_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    mapping = db.scalar(select(Mapping).where(Mapping.id == mapping_id, Mapping.job_id == job.id))
    if mapping is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    mapping_service.reject_mapping(mapping, principal.user_id)
    record_audit(db, AuditAction.MAPPING_APPROVE, org_id=principal.org_id, user_id=principal.user_id, entity_type="mapping", entity_id=str(mapping.id), ip=client_ip(request),
                 detail={"rejected": True})  # fmt: skip
    return {"id": str(mapping.id), "status": mapping.status.value}


# ------------------------------------------------------------------ rules


@router.post("/rules/generate")
def generate_rules(job_id: uuid.UUID, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    try:
        result = service.generate(db, job)
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None
    return {**asdict(result), "status": job.status.value}


@router.get("/rules")
def list_rules(job_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> list[dict[str, Any]]:
    job = _job(db, job_id)
    rows = db.scalars(select(TransformRule).where(TransformRule.job_id == job.id).order_by(TransformRule.target_table, TransformRule.target_field)).all()
    return [_rule_out(r) for r in rows]


@router.put("/rules/{rule_id}")
def put_rule(job_id: uuid.UUID, rule_id: uuid.UUID, body: StepsRequest, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    rule = rules_module.find_rule(db, job.id, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    try:
        service.update_rule(db, job, rule, body.steps)
    except rules_module.RuleRejected as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None
    return _rule_out(rule)


@router.put("/rules/{rule_id}/code-map")
def put_code_map(job_id: uuid.UUID, rule_id: uuid.UUID, body: CodeMapRequest, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    rule = rules_module.find_rule(db, job.id, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    try:
        service.set_code_map(db, job, rule, body.mapping, body.passthrough)
    except rules_module.RuleRejected as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None
    return _rule_out(rule)


# ------------------------------------------------------------------ dry run, load, rollback, reports


@router.post("/dry-run")
def dry_run(job_id: uuid.UUID, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    try:
        run = service.dry_run(db, job)
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None
    return {"status": job.status.value, "ready": run.ready, "blocking": run.blocking, "warnings": run.warnings, "tables": [asdict(t) for t in run.tables]}


@router.post("/load")
def load(job_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    try:
        outcome = service.load_job(db, job)
    except service.LoadBlocked as exc:
        raise _conflict(exc) from None
    except service.LoadFailed as exc:
        db.commit()  # keep the FAILED state, its event and the failed report
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"message": "The load failed and was rolled back", "failed_checks": (exc.report or {}).get("failed_checks", [])}) from None
    record_audit(db, AuditAction.JOB_LOAD, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request),
                 detail={"loaded": outcome.loaded, "quarantined": outcome.quarantined})  # fmt: skip
    return {"status": job.status.value, "passed": outcome.passed, "loaded": outcome.loaded, "quarantined": outcome.quarantined, "seconds": outcome.seconds,
            "quality_before": outcome.quality_before, "quality_after": outcome.quality_after, "tables": outcome.tables}  # fmt: skip


@router.post("/rollback")
def rollback(job_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    try:
        result = service.rollback_job(db, job)
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None
    record_audit(db, AuditAction.JOB_ROLLBACK, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request), detail=result["removed"])
    return {"status": job.status.value, **result}


@router.get("/reconciliation")
def reconciliation(job_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    report = service.get_report(db, job.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This job has no reconciliation report")
    return {"passed": report.passed, "source_rows": report.source_rows, "loaded_rows": report.loaded_rows, "quarantined_rows": report.quarantined_rows,
            "quality_before": float(report.quality_before) if report.quality_before is not None else None,
            "quality_after": float(report.quality_after) if report.quality_after is not None else None, "checks": report.checks}  # fmt: skip


@router.get("/quarantine")
def quarantine(
    job_id: uuid.UUID, reason: str | None = None, table: str | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
    _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db),
) -> dict[str, Any]:
    job = _job(db, job_id)
    total, rows = service.quarantine_page(db, job.id, reason, table, limit, offset)
    return {"total": total, "rows": [{"table": r.target_table, "row": r.source_row_number, "reason": r.reason_code, "detail": r.reason_detail, "raw": r.raw_row} for r in rows],
            "status": job.status.value if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ROLLED_BACK) else None}  # fmt: skip
