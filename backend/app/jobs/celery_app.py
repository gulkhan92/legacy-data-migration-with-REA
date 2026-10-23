"""The Celery application. Redis is the broker; results are not stored, a job's progress lives in job_events."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings


def make_celery() -> Celery:
    settings = get_settings()
    app = Celery("legacylift", broker=settings.redis_url, include=["app.jobs.tasks"])
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_default_queue="legacylift",
        task_ignore_result=True,
        # A task is acknowledged after it finished, so a worker that dies mid-stage hands it to another worker. The
        # stages are idempotent and the job lock keeps two of them from running together.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=settings.job_task_time_limit_seconds,
        task_soft_time_limit=max(1, settings.job_task_time_limit_seconds - 60),
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": settings.job_task_time_limit_seconds + 600, "socket_connect_timeout": 2, "socket_timeout": 5},
        # Publishing from the API must fail fast when Redis is down instead of hanging the request.
        task_publish_retry=True,
        task_publish_retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5},
        # Run by `celery beat`: the metrics snapshot every night, the invoices of the previous month on the first.
        beat_schedule={
            "nightly-snapshot": {"task": "legacylift.nightly_snapshot", "schedule": crontab(hour=0, minute=30)},
            "monthly-invoices": {"task": "legacylift.monthly_invoices", "schedule": crontab(day_of_month=1, hour=1, minute=0)},
        },
        task_always_eager=settings.jobs_execution == "eager",
        task_eager_propagates=False,  # retries must run inside Celery; dispatch re-raises what really failed
    )
    return app


celery_app = make_celery()
