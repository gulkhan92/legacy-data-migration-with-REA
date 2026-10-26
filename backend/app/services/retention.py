"""Retention of uploaded files: the encrypted copy of an upload is deleted once the organization's retention period has passed and none of
its jobs is still running. Migrated data, reports, the quarantine and the audit log are not touched; only the file that could be
analysed again is removed, and the source row keeps its name, size and checksum so the history still reads.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Job, Source
from app.db.repositories.jobs import TERMINAL_STATUSES
from app.db.session import make_session_factory, scoped_session
from app.services.audit import AuditAction, record_audit
from app.services.uploads import delete_upload

log = logging.getLogger(__name__)


def expired_sources(db: Session, cutoff: datetime) -> list[Source]:
    """Sources with a stored file that are older than the cutoff, whose jobs have all finished, none of them after the cutoff."""
    out = []
    for source in db.scalars(select(Source).where(Source.storage_path.is_not(None), Source.created_at < cutoff)):
        uses = (Job.source_id == source.id) | (Job.data_source_id == source.id)
        if db.scalar(select(func.count()).select_from(Job).where(uses, Job.status.notin_(list(TERMINAL_STATUSES)))):
            continue
        latest = db.scalar(select(func.max(Job.updated_at)).where(uses))
        if latest is not None and latest >= cutoff:
            continue
        out.append(source)
    return out


def purge_expired_uploads(*, now: datetime | None = None, factory: sessionmaker[Session] | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    factory = factory or make_session_factory()
    with scoped_session(factory=factory) as db:
        orgs = [(row[0], int(row[1])) for row in db.execute(text("SELECT org_id, retention_days FROM platform_orgs_with_uploads()"))]
    deleted = 0
    for org_id, days in orgs:
        try:
            with scoped_session(org_id=org_id, factory=factory) as db:
                removed = 0
                for source in expired_sources(db, now - timedelta(days=days)):
                    path, source.storage_path = source.storage_path, None
                    if path:
                        delete_upload(path)
                    removed += 1
                if removed:
                    record_audit(db, AuditAction.RETENTION_PURGE, org_id=org_id, entity_type="source", detail={"files_deleted": removed, "retention_days": days})
                deleted += removed
        except Exception:  # one organization must not stop the others
            log.exception("retention purge failed for %s", org_id)
    return {"organizations": len(orgs), "files_deleted": deleted}
