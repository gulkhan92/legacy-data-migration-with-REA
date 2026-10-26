import csv
import io
import json
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.core.security import Principal
from app.db.models import Job, Mapping, QuarantinedRow, TargetField, TransformRule, UsageEvent
from app.db.models.enums import JobStatus, MappingStatus, Role
from app.db.session import make_session_factory, scoped_session
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


def _cost(db: Session, job: Job, run: Any) -> dict[str, Any]:
    """What loading this job will cost: model tokens so far, and a rough time for the load. The time comes from how fast this
    organization's earlier loads ran (the median of the last ones) and, when there are fewer than three, from how fast the sample was transformed."""
    rows = sum(t.source_rows or 0 for t in run.tables)
    speeds = [float(e.records_loaded) / (e.duration_ms / 1000) for e in db.scalars(select(UsageEvent).where(UsageEvent.duration_ms > 0, UsageEvent.records_loaded > 0).order_by(UsageEvent.occurred_at.desc()).limit(20))]
    if len(speeds) >= 3:
        speeds.sort()
        rate, basis = speeds[len(speeds) // 2], f"the median speed of this organization's last {len(speeds)} loads"
    else:
        sample_rows, sample_seconds = sum(t.sample_rows for t in run.tables), sum(t.seconds for t in run.tables)
        rate, basis = (sample_rows / sample_seconds / 3 if sample_seconds > 0 else 0.0), "the speed of the sample, with a margin for staging and checks (no earlier loads to go by)"
    return {"source_rows": rows, "tokens_used": job.tokens_used, "token_budget": job.token_budget, "estimated_seconds": round(rows / rate, 1) if rate > 0 else None, "basis": basis}


@router.post("/dry-run")
def dry_run(job_id: uuid.UUID, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    try:
        run = service.dry_run(db, job)
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None
    return {"status": job.status.value, "ready": run.ready, "blocking": run.blocking, "warnings": run.warnings, "tables": [asdict(t) for t in run.tables],
            "cost": _cost(db, job, run)}  # fmt: skip


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


# ------------------------------------------------------------------ editing and bulk approving mappings


class MappingPatch(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"target_field_id": "9a7e0d62-5a0c-4a3a-8f0b-3c7e8f4f2b11", "transform_hint": "cents to decimal"}]})

    target_field_id: uuid.UUID | None = Field(None, description="Another target field; null rejects the mapping. Leave it out to keep the target")
    transform_hint: str | None = Field(None, max_length=500)


class BulkApprove(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"mapping_ids": ["3f1c1a3e-0a6c-4cf5-a2a7-6c0f9d5f6a01"]}]})

    mapping_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


EDITABLE = (JobStatus.MAPPED, JobStatus.AWAITING_REVIEW)


@router.patch("/mappings/{mapping_id}", summary="Change the target or the transform hint of a mapping")
def patch_mapping(
    job_id: uuid.UUID, mapping_id: uuid.UUID, body: MappingPatch, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)
) -> dict[str, Any]:
    job = _job(db, job_id)
    mapping = db.scalar(select(Mapping).where(Mapping.id == mapping_id, Mapping.job_id == job.id))
    if mapping is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mapping not found")
    if job.status not in EDITABLE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Mappings can be changed while the job is MAPPED or AWAITING_REVIEW, not {job.status.value}")
    changes = body.model_fields_set
    if "transform_hint" in changes:
        mapping.transform_hint = body.transform_hint
    if "target_field_id" in changes:
        if body.target_field_id is None:
            mapping_service.reject_mapping(mapping, principal.user_id)
        else:
            field = db.get(TargetField, body.target_field_id)
            if field is None or field.schema_id != job.target_schema_id:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="That field is not part of the job's target schema")
            mapping_service.approve_mapping(db, mapping, principal.user_id, target_field_id=field.id)
    db.flush()
    record_audit(db, AuditAction.MAPPING_APPROVE, org_id=principal.org_id, user_id=principal.user_id, entity_type="mapping", entity_id=str(mapping.id), ip=client_ip(request), detail={"changed": sorted(changes)})
    return {"id": str(mapping.id), "status": mapping.status.value, "method": mapping.method.value, "target_field_id": str(mapping.target_field_id) if mapping.target_field_id else None,
            "transform_hint": mapping.transform_hint, "regenerate_rules": job.status is JobStatus.AWAITING_REVIEW}  # fmt: skip


@router.post("/mappings/bulk-approve", summary="Approve several mappings at once")
def bulk_approve(job_id: uuid.UUID, body: BulkApprove, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    ids = list(dict.fromkeys(body.mapping_ids))
    found = {m.id: m for m in db.scalars(select(Mapping).where(Mapping.job_id == job.id, Mapping.id.in_(ids)))}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"message": "Some mappings do not belong to this job", "ids": missing})
    approved, skipped = 0, []
    for mapping_id in ids:
        mapping = found[mapping_id]
        if mapping.target_field_id is None or mapping.status is MappingStatus.REJECTED:
            skipped.append({"id": str(mapping_id), "reason": "no target" if mapping.target_field_id is None else "rejected"})
            continue
        mapping_service.approve_mapping(db, mapping, principal.user_id)
        approved += 1
    record_audit(db, AuditAction.MAPPING_APPROVE, org_id=principal.org_id, user_id=principal.user_id, entity_type="job", entity_id=str(job.id), ip=client_ip(request), detail={"approved": approved, "mode": "bulk"})
    return {"approved": approved, "skipped": skipped}


# ------------------------------------------------------------------ transform preview


class PreviewRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"target_table": "orders", "target_field": "order_date", "steps": [{"op": "parse_date", "formats": ["%Y-%m-%d", "%d/%m/%Y"]}], "rows": 10}]})

    target_table: str = Field(min_length=1, max_length=100)
    target_field: str = Field(min_length=1, max_length=100)
    steps: list[dict[str, Any]] | None = Field(None, min_length=1, max_length=12, description="Steps to try. Leave out to preview the saved rule")
    rows: int = Field(10, ge=1, le=50)


@router.post("/transforms/preview", summary="Try a rule on a sample of the source without saving it")
def preview_transform(job_id: uuid.UUID, body: PreviewRequest, _: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> dict[str, Any]:
    job = _job(db, job_id)
    rule = db.scalar(select(TransformRule).where(TransformRule.job_id == job.id, TransformRule.target_table == body.target_table, TransformRule.target_field == body.target_field))
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This job has no rule for that field; generate the rules first")
    try:
        return service.preview_rule(db, job, rule, body.steps, body.rows)
    except rules_module.RuleRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    except service.TransformUnavailable as exc:
        raise _conflict(exc) from None


# ------------------------------------------------------------------ CSV export of the quarantine


def _safe_cell(value: Any) -> str:
    """A cell that a spreadsheet would read as a formula gets a quote in front, so exported data cannot run anything."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _line(cells: list[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow(cells)
    return buffer.getvalue()


def _csv_rows(org_id: uuid.UUID, user_id: uuid.UUID, job_id: uuid.UUID, reason: str | None, table: str | None) -> Iterator[str]:
    yield _line(["table", "row", "reason", "detail", "raw_row"])
    with scoped_session(org_id=org_id, user_id=user_id, factory=make_session_factory()) as db:
        stmt = select(QuarantinedRow).where(QuarantinedRow.job_id == job_id)
        if reason:
            stmt = stmt.where(QuarantinedRow.reason_code == reason)
        if table:
            stmt = stmt.where(QuarantinedRow.target_table == table)
        stmt = stmt.order_by(QuarantinedRow.target_table, QuarantinedRow.source_row_number).execution_options(yield_per=500)
        for row in db.scalars(stmt):
            yield _line([_safe_cell(row.target_table), row.source_row_number, _safe_cell(row.reason_code), _safe_cell(row.reason_detail), _safe_cell(json.dumps(row.raw_row, default=str))])


@router.get("/quarantine.csv", summary="Export the quarantined rows as CSV", response_class=StreamingResponse, responses={200: {"content": {"text/csv": {}}}})
def quarantine_csv(job_id: uuid.UUID, reason: str | None = None, table: str | None = None, principal: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> StreamingResponse:
    job = _job(db, job_id)
    headers = {"Content-Disposition": f'attachment; filename="quarantine-{job.id}.csv"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    return StreamingResponse(_csv_rows(principal.org_id, principal.user_id, job.id, reason, table), media_type="text/csv; charset=utf-8", headers=headers)
