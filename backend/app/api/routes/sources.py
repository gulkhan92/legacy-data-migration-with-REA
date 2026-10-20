from collections.abc import Iterator

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_principal, get_tenant_db, require_role
from app.api.schemas import SourceOut
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.security import Principal
from app.db.models import Source
from app.db.models.enums import Role
from app.db.session import scoped_session
from app.services.audit import AuditAction, record_audit
from app.services.uploads import UploadRejected, read_limited, store_upload

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
