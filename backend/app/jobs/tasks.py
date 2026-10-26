"""Celery tasks. A task is a thin wrapper that turns the stage runner's exceptions into retries."""

import logging
import uuid
from typing import Any

from app.core.config import get_settings
from app.jobs.celery_app import celery_app
from app.jobs.stages import Outcome, Stage, StageBusy, StageResult, TransientStageError, fail_job, run_stage

log = logging.getLogger(__name__)

TASK_NAME = "legacylift.run_stage"


@celery_app.task(bind=True, name=TASK_NAME, max_retries=100)
def run_stage_task(self, stage: str, job_id: str, org_id: str, user_id: str | None, params: dict[str, Any] | None = None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    settings = get_settings()
    job, org, user = uuid.UUID(job_id), uuid.UUID(org_id), uuid.UUID(user_id) if user_id else None
    try:
        return run_stage(Stage(stage), job, org, user, params).as_dict()
    except StageBusy as exc:
        if self.request.retries >= settings.job_stage_busy_retries:
            return StageResult(stage, Outcome.SKIPPED, "Another stage of this job kept running").as_dict()
        raise self.retry(exc=exc, countdown=5) from None
    except TransientStageError as exc:
        if self.request.retries >= settings.job_task_max_retries:
            fail_job(org, user, job, stage, f"Stage {stage} failed after {self.request.retries} retries ({exc})", {"error": str(exc), "retries": self.request.retries})
            return StageResult(stage, Outcome.FAILED, f"Failed after {self.request.retries} retries").as_dict()
        countdown = min(settings.job_task_retry_base_seconds * 2**self.request.retries, 120)
        log.info("retrying stage %s of job %s in %ss", stage, job_id, countdown)
        raise self.retry(exc=exc, countdown=countdown) from None


@celery_app.task(name="legacylift.nightly_snapshot")
def nightly_snapshot_task() -> list[str]:
    """Close yesterday and refresh today (scheduled every night)."""
    from app.billing import snapshots

    return [d.isoformat() for d in snapshots.nightly()]


@celery_app.task(name="legacylift.monthly_invoices")
def monthly_invoices_task() -> dict[str, int]:
    """Invoice the month that just ended (scheduled on the first of every month). Running it again changes nothing."""
    from datetime import UTC, datetime

    from app.billing import invoicing

    run = invoicing.run_invoices(invoicing.previous_month(datetime.now(UTC).date()))
    return {"created": run.created, "skipped": run.skipped, "failed": len(run.failed)}


@celery_app.task(name="legacylift.purge_uploads")
def purge_uploads_task() -> dict[str, int]:
    """Delete the encrypted copies of uploads that have outlived their organization's retention period (scheduled every night)."""
    from app.services import retention

    return retention.purge_expired_uploads()
