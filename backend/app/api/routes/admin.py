from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_tenant_db, require_role
from app.api.schemas import AuditEntryOut
from app.core.security import Principal
from app.db.models import AuditLog

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/audit-log", response_model=list[AuditEntryOut])
def audit_log(
    action: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _: Principal = Depends(require_role()),  # admin only
    db: Session = Depends(get_tenant_db),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 200)).offset(max(offset, 0))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return list(db.scalars(stmt))
