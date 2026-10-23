import ipaddress
import uuid
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.db.models import AuditLog


class AuditAction:
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGIN_LOCKED = "auth.login_locked"
    LOGOUT = "auth.logout"
    REFRESH_REUSE = "auth.refresh_reuse_detected"
    UPLOAD = "source.upload"
    UPLOAD_REJECTED = "source.upload_rejected"
    DISCOVERY = "discovery.run"
    DISCOVERY_FAILED = "discovery.failed"
    PROFILE = "profile.run"
    MAPPING_RUN = "mapping.run"
    MAPPING_APPROVE = "mapping.approve"
    JOB_LOAD = "job.load"
    JOB_ROLLBACK = "job.rollback"
    SETTINGS_CHANGE = "settings.change"
    JOB_CREATE = "job.create"
    JOB_RUN = "job.run"
    JOB_CANCEL = "job.cancel"
    SOURCE_DELETE = "source.delete"
    SAMPLE_REGISTER = "source.sample"
    USER_CREATE = "user.create"
    USER_ROLE_CHANGE = "user.role_change"
    USER_REMOVE = "user.remove"


def _clean_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def record_audit(
    session: Session,
    action: str,
    *,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry.

    A Core insert without RETURNING is used on purpose: rows without an organization are not
    visible to any tenant, so reading them back inside the same statement would be refused.
    The session must be scoped to org_id when org_id is given.
    """
    session.execute(
        insert(AuditLog.__table__).values(
            org_id=org_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            ip_address=_clean_ip(ip),
            detail=detail or {},
        ).inline()  # no RETURNING
    )
