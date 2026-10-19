import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.job_state import check_transition
from app.db.models import Job, JobEvent
from app.db.models.enums import JobStatus
from app.db.repositories.base import Repository

TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ROLLED_BACK})


class JobRepository(Repository[Job]):
    model = Job

    def transition(
        self,
        job: Job,
        target: JobStatus,
        *,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> Job:
        """Move a job along the state machine and record the event in the same transaction."""
        check_transition(job.status, target)
        previous = job.status
        job.status = target
        now = datetime.now(UTC)
        if target is JobStatus.DISCOVERING and job.started_at is None:
            job.started_at = now
        if target in TERMINAL_STATUSES:
            job.completed_at = now
        if target is JobStatus.FAILED and message:
            job.error_message = message
        self.session.add(
            JobEvent(
                org_id=job.org_id,
                job_id=job.id,
                from_status=previous,
                to_status=target,
                message=message,
                detail=detail or {},
            )
        )
        self.session.flush()
        return job

    def events(self, job_id: uuid.UUID) -> list[JobEvent]:
        stmt = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
        return list(self.session.scalars(stmt))
