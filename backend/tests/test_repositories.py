import uuid

import pytest
from sqlalchemy import select

from app.db import models
from app.db.job_state import ALLOWED_TRANSITIONS, InvalidTransition
from app.db.models.enums import JobStatus, SourceKind
from app.db.repositories import (
    JobRepository,
    MappingMemoryRepository,
    Repository,
    TargetFieldRepository,
    WarehouseRepository,
)
from app.db.session import tenant_session


def _vector(*hot: int) -> list[float]:
    vec = [0.0] * 384
    for i in hot:
        vec[i] = 1.0
    return vec


class SourceRepository(Repository[models.Source]):
    model = models.Source


def _new_job(session, org_id: uuid.UUID) -> models.Job:  # type: ignore[no-untyped-def]
    source = SourceRepository(session).add(models.Source(name="s", kind=SourceKind.CSV))
    return JobRepository(session).add(models.Job(source_id=source.id))


def test_add_fills_org_from_session_tenant(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        source = SourceRepository(session).add(models.Source(name="auto-org", kind=SourceKind.CSV))
        assert source.org_id == a.org_id
        assert SourceRepository(session).count(name="auto-org") == 1


def test_repository_list_and_get_are_tenant_scoped(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(a.org_id, app_factory) as session:
        repo = SourceRepository(session)
        assert repo.get(a.source_id) is not None
        assert repo.get(b.source_id) is None
        assert all(s.org_id == a.org_id for s in repo.list(limit=100))


def test_job_walks_the_whole_pipeline_and_records_events(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    path = [
        JobStatus.UPLOADED, JobStatus.DISCOVERING, JobStatus.PROFILED, JobStatus.MAPPED,
        JobStatus.AWAITING_REVIEW, JobStatus.DRY_RUN, JobStatus.READY, JobStatus.LOADING,
        JobStatus.RECONCILING, JobStatus.COMPLETED, JobStatus.ROLLED_BACK,
    ]  # fmt: skip
    with tenant_session(a.org_id, app_factory) as session:
        jobs = JobRepository(session)
        job = _new_job(session, a.org_id)
        assert job.status is JobStatus.CREATED
        for target in path:
            jobs.transition(job, target)
        events = jobs.events(job.id)
        assert [e.to_status for e in events] == path
        assert events[0].from_status is JobStatus.CREATED
        assert job.started_at is not None and job.completed_at is not None


def test_invalid_transition_is_rejected_and_state_unchanged(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        jobs = JobRepository(session)
        job = _new_job(session, a.org_id)
        with pytest.raises(InvalidTransition):
            jobs.transition(job, JobStatus.LOADING)
        assert job.status is JobStatus.CREATED
        assert jobs.events(job.id) == []


def test_failed_job_keeps_error_message_and_can_be_rolled_back(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        jobs = JobRepository(session)
        job = _new_job(session, a.org_id)
        jobs.transition(job, JobStatus.FAILED, message="source unreadable")
        assert job.error_message == "source unreadable"
        jobs.transition(job, JobStatus.ROLLED_BACK)


def test_every_status_is_covered_by_the_state_machine() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(JobStatus)
    assert ALLOWED_TRANSITIONS[JobStatus.ROLLED_BACK] == frozenset()
    for targets in ALLOWED_TRANSITIONS.values():
        assert JobStatus.CREATED not in targets


def test_target_field_nearest_uses_cosine_similarity(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        schema_row = models.TargetSchema(org_id=a.org_id, name=f"vec-{uuid.uuid4().hex[:6]}")
        session.add(schema_row)
        session.flush()
        for name, hot in (("email", (1,)), ("phone", (2,)), ("email_alt", (1, 3))):
            session.add(
                models.TargetField(
                    org_id=a.org_id,
                    schema_id=schema_row.id,
                    table_name="customers",
                    name=name,
                    data_type="text",
                    description=name,
                    embedding=_vector(*hot),
                )
            )
        session.flush()
        best = TargetFieldRepository(session).nearest(_vector(1), schema_id=schema_row.id, limit=3)
        assert [f.name for f, _ in best] == ["email", "email_alt", "phone"]
        assert best[0][1] == pytest.approx(1.0)
        assert best[1][1] == pytest.approx(0.7071, abs=1e-3)
        assert best[2][1] == pytest.approx(0.0, abs=1e-6)


def test_mapping_memory_nearest_is_tenant_scoped(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    with tenant_session(b.org_id, app_factory) as session:
        session.add(
            models.MappingMemory(
                org_id=b.org_id,
                source_signature="only-bravo",
                source_text="x",
                target_field_id=b.field_id,
                embedding=_vector(9),
            )
        )
    with tenant_session(a.org_id, app_factory) as session:
        hits = MappingMemoryRepository(session).nearest(_vector(9), limit=10)
        assert all(m.org_id == a.org_id for m, _ in hits)
        assert "only-bravo" not in {m.source_signature for m, _ in hits}


def test_unique_constraints_prevent_duplicate_mapping_per_column(app_factory, tenants, admin_engine) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    a, _ = tenants
    with pytest.raises(IntegrityError), tenant_session(a.org_id, app_factory) as session:
        column = session.scalars(select(models.SourceColumn)).first()
        for _ in range(2):
            session.add(
                models.Mapping(
                    org_id=a.org_id, job_id=a.job_id, source_column_id=column.id, method="embedding"
                )
            )
            session.flush()


def test_rollback_by_job_id_removes_only_that_jobs_rows(app_factory, tenants) -> None:  # type: ignore[no-untyped-def]
    a, _ = tenants
    with tenant_session(a.org_id, app_factory) as session:
        job_x = _new_job(session, a.org_id)
        job_y = _new_job(session, a.org_id)
        for job in (job_x, job_y):
            patient = models.Patient(org_id=a.org_id, job_id=job.id, first_name="p")
            session.add(patient)
            session.flush()
            session.add(models.Encounter(org_id=a.org_id, job_id=job.id, patient_id=patient.id))
            session.add(models.Observation(org_id=a.org_id, job_id=job.id, patient_id=patient.id, code="1"))
        session.flush()

        removed = WarehouseRepository(session).delete_by_job(job_x.id)
        assert removed["patients"] == 1
        assert removed["encounters"] == 1
        assert removed["observations"] == 1
        assert session.scalars(select(models.Patient).where(models.Patient.job_id == job_x.id)).all() == []
        assert len(session.scalars(select(models.Patient).where(models.Patient.job_id == job_y.id)).all()) == 1
