"""Hand stages to the workers. The only place the API touches the queue."""

import uuid
from typing import Any

from celery import chain
from celery.result import EagerResult
from kombu.exceptions import KombuError
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.jobs.celery_app import celery_app
from app.jobs.stages import Stage
from app.jobs.tasks import run_stage_task


class QueueUnavailable(Exception):
    """The broker did not accept the task."""


def enqueue(stages: list[Stage], job_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Queue the stages of a job in order. Each later stage only starts when the earlier one returned."""
    settings = get_settings()
    celery_app.conf.task_always_eager = settings.jobs_execution == "eager"
    signatures = [run_stage_task.si(stage.value, str(job_id), str(org_id), str(user_id) if user_id else None, params or {}) for stage in stages]
    try:
        result = signatures[0].apply_async() if len(signatures) == 1 else chain(*signatures).apply_async()
    except (KombuError, RedisError, OSError) as exc:
        raise QueueUnavailable("The job queue is not available") from exc
    if isinstance(result, EagerResult) and result.failed():
        raise result.result  # eager mode only: a bug in a task shows up at once instead of being stored
    return {"task_id": str(result.id), "execution": settings.jobs_execution}
