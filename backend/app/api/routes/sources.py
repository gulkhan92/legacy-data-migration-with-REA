import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.api.schemas import SourceOut
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import Principal
from app.db.models import Job, Source
from app.db.models.enums import Role
from app.db.session import scoped_session
from app.services.audit import AuditAction, record_audit
from app.services.uploads import UploadRejected, delete_upload, read_limited, store_upload, upload_exists

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])


def _chunks(upload: UploadFile) -> Iterator[bytes]:
    while chunk := upload.file.read(1024 * 1024):
        yield chunk


@router.get("", response_model=list[SourceOut])
def list_sources(
    limit: int = 50,
    offset: int = 0,
    _: Principal = Depends(get_principal),
    db: Session = Depends(get_tenant_db),
) -> list[Source]:
    stmt = select(Source).order_by(Source.created_at.desc()).limit(min(limit, 200)).offset(max(offset, 0))
    return list(db.scalars(stmt))


@router.post("/upload", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def upload_source(
    request: Request,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_role(Role.ANALYST)),
    db: Session = Depends(get_tenant_db),
) -> Source:
    ip = client_ip(request)
    declared = request.headers.get("content-length")
    try:
        if declared and declared.isdigit() and int(declared) > get_settings().upload_max_bytes + 4096:
            raise UploadRejected("The file is too large", status_code=413)
        data = read_limited(_chunks(file))
        stored = store_upload(principal.org_id, file.filename or "upload", data)
    except UploadRejected as exc:
        # Recorded in its own transaction because the request session rolls back on error.
        with scoped_session(org_id=principal.org_id, user_id=principal.user_id) as audit_db:
            record_audit(
                audit_db,
                AuditAction.UPLOAD_REJECTED,
                org_id=principal.org_id,
                user_id=principal.user_id,
                ip=ip,
                detail={"reason": exc.reason},
            )
        raise HTTPException(status_code=exc.status_code, detail=exc.reason) from None

    source = Source(
        org_id=principal.org_id,
        name=stored.original_filename,
        kind=stored.kind,
        original_filename=stored.original_filename,
        storage_path=stored.storage_path,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        encrypted=True,
        created_by=principal.user_id,
    )
    db.add(source)
    db.flush()
    record_audit(
        db,
        AuditAction.UPLOAD,
        org_id=principal.org_id,
        user_id=principal.user_id,
        entity_type="source",
        entity_id=str(source.id),
        ip=ip,
        detail={"kind": stored.kind.value, "size_bytes": stored.size_bytes},
    )
    return source


# ------------------------------------------------------------------ one source, delete, sample systems

SAMPLES: dict[str, dict[str, Any]] = {
    "commerce": {
        "title": "Commerce (legacy SQLite)", "description": "A small shop database with cryptic table and column names, mixed date and money formats and injected defects.",
        "files": ["commerce/commerce_legacy.sqlite"], "target_schema": "commerce",
    },
    "clinic": {
        "title": "ClinicDesk (.NET binary)", "description": "A Windows clinic application: a .NET assembly and its binary data file, no database and no documentation.",
        "files": ["clinic/ClinicDesk.dll", "clinic/clinic.cdb"], "target_schema": "clinic",
    },
}  # fmt: skip


def _sample_root() -> Path:
    return Path(__file__).resolve().parents[4] / "datasets" / "fixtures"


def _available(name: str) -> bool:
    return all((_sample_root() / f).is_file() for f in SAMPLES[name]["files"])


class SampleOut(BaseModel):
    name: str
    title: str
    description: str
    target_schema: str
    files: list[str]
    available: bool


class SampleRegistered(BaseModel):
    sample: str
    target_schema: str
    sources: list[SourceOut]
    source_id: uuid.UUID
    data_source_id: uuid.UUID | None


@router.get("/samples", response_model=list[SampleOut], summary="The bundled sample systems")
def list_samples(_: Principal = Depends(get_principal)) -> list[SampleOut]:
    return [SampleOut(name=n, title=s["title"], description=s["description"], target_schema=s["target_schema"], files=[Path(f).name for f in s["files"]], available=_available(n))
            for n, s in SAMPLES.items()]  # fmt: skip


@router.post("/samples/{name}", response_model=SampleRegistered, status_code=status.HTTP_201_CREATED, summary="Add a sample system to the organization's sources")
def register_sample(name: str, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> SampleRegistered:
    sample = SAMPLES.get(name)
    if sample is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown sample system")
    if not _available(name):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The sample files are not installed on this server")
    sources: list[Source] = []
    for relative in sample["files"]:
        path = _sample_root() / relative
        data = path.read_bytes()
        stored = store_upload(principal.org_id, path.name, data)
        existing = db.scalar(select(Source).where(Source.is_sample.is_(True), Source.sha256 == stored.sha256))
        if existing is not None:  # registering twice gives the same sources back
            if existing.storage_path and upload_exists(existing.storage_path):
                delete_upload(stored.storage_path)
            else:  # the row is there but its file is not (storage was replaced): keep the fresh copy
                existing.storage_path = stored.storage_path
            sources.append(existing)
            continue
        source = Source(org_id=principal.org_id, name=path.name, kind=stored.kind, original_filename=stored.original_filename, storage_path=stored.storage_path,
                        size_bytes=stored.size_bytes, sha256=stored.sha256, is_sample=True, encrypted=True, created_by=principal.user_id)  # fmt: skip
        db.add(source)
        db.flush()
        sources.append(source)
    record_audit(db, AuditAction.SAMPLE_REGISTER, org_id=principal.org_id, user_id=principal.user_id, ip=client_ip(request), detail={"sample": name})
    return SampleRegistered(sample=name, target_schema=sample["target_schema"], sources=[SourceOut.model_validate(s) for s in sources], source_id=sources[0].id,
                            data_source_id=sources[1].id if len(sources) > 1 else None)  # fmt: skip


@router.get("/{source_id}", response_model=SourceOut, summary="One source")
def get_source(source_id: uuid.UUID, _: Principal = Depends(get_principal), db: Session = Depends(get_tenant_db)) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a source and its stored file")
def delete_source(source_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role(Role.ANALYST)), db: Session = Depends(get_tenant_db)) -> None:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    used = db.scalar(select(func.count()).select_from(Job).where((Job.source_id == source.id) | (Job.data_source_id == source.id))) or 0
    if used:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{used} job(s) use this source; delete or roll back them first")
    path = source.storage_path
    db.delete(source)
    record_audit(db, AuditAction.SOURCE_DELETE, org_id=principal.org_id, user_id=principal.user_id, entity_type="source", entity_id=str(source.id), ip=client_ip(request), detail={"name": source.name})
    db.flush()
    if path:
        delete_upload(path)
