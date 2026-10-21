"""Run profiling for a job and persist the profile and the quality report."""

import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.job_state import InvalidTransition
from app.db.models import DiscoveryEvidence, Job, Source, SourceColumn
from app.db.models.enums import JobStatus, SourceKind
from app.db.repositories.jobs import JobRepository
from app.discovery import service as discovery_service
from app.discovery.models import LayoutSpec
from app.discovery.rea_adapter import decrypted_workspace
from app.profiling.engine import profile_and_check
from app.profiling.models import ProfileResult, QualityReport
from app.profiling.readers import DEFAULT_CHUNK, BinaryReader, CsvReader, SqliteReader, TableSource

RESULT_PROFILE = "result:profile"
RESULT_QUALITY = "result:quality"


class ProfilingUnavailable(Exception):
    """The job cannot be profiled yet (for example discovery has not run)."""


@dataclass
class ProfilingOutcome:
    profile: ProfileResult
    quality: QualityReport


def _tables_for(session: Session, job: Job, source: Source, stack: ExitStack) -> list[TableSource]:
    if source.kind is SourceKind.SQLITE:
        path = stack.enter_context(decrypted_workspace(source.storage_path or "", "source.sqlite"))
        return SqliteReader(path).tables()
    if source.kind is SourceKind.CSV:
        path = stack.enter_context(decrypted_workspace(source.storage_path or "", "source.csv"))
        return CsvReader(path, name=Path(source.original_filename or "csv").stem or "csv").tables()
    # A proprietary binary file is decoded with the layout that discovery recovered.
    layout_json = discovery_service.load_result(session, job.id)
    if layout_json is None or "tables" not in layout_json or "method" not in layout_json:
        raise ProfilingUnavailable("Run discovery first: the record layout of this source is not known yet")
    layout = LayoutSpec.model_validate(layout_json)
    is_assembly = Path(source.original_filename or "").suffix.lower() in discovery_service.ASSEMBLY_EXTENSIONS
    data = session.get(Source, job.data_source_id) if is_assembly and job.data_source_id else (None if is_assembly else source)
    if data is None:
        raise ProfilingUnavailable("Attach the data file to profile this source")
    path = stack.enter_context(decrypted_workspace(data.storage_path or "", "source.cdb"))
    return BinaryReader(path, layout).tables()


def persist(session: Session, job: Job, source: Source, outcome: ProfilingOutcome) -> None:
    session.execute(delete(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job.id, DiscoveryEvidence.subject.in_([RESULT_PROFILE, RESULT_QUALITY])))
    existing = {(c.table_name, c.column_name): c for c in session.scalars(select(SourceColumn).where(SourceColumn.job_id == job.id))}
    for table in outcome.profile.tables:
        for p in table.columns:
            row = existing.get((table.name, p.name))
            if row is None:
                row = SourceColumn(org_id=job.org_id, job_id=job.id, table_name=table.name, column_name=p.name, profile={})
                session.add(row)
            row.normalized_name = p.expanded_name or None
            row.inferred_type = p.inferred_type
            row.semantic_type = p.semantic_type
            row.pattern_class = (p.pattern_class or "")[:100] or None
            row.null_rate = p.null_rate
            row.distinct_count = p.distinct_count
            row.pii = p.pii
            row.profile = {**(row.profile or {}), "stats": p.model_dump(mode="json")}
    version = None
    session.add(
        DiscoveryEvidence(org_id=job.org_id, job_id=job.id, subject=RESULT_PROFILE, command="profile", rea_version=version,
                          raw_json=outcome.profile.model_dump(mode="json"))  # fmt: skip
    )
    session.add(
        DiscoveryEvidence(org_id=job.org_id, job_id=job.id, subject=RESULT_QUALITY, command="quality", rea_version=version,
                          raw_json=outcome.quality.model_dump(mode="json"))  # fmt: skip
    )


def run_profiling(session: Session, job: Job, *, chunk_size: int = DEFAULT_CHUNK) -> ProfilingOutcome:
    """Profile the job's source. Moves the job to PROFILED (a CSV source passes through DISCOVERING first)."""
    jobs = JobRepository(session)
    source = session.get(Source, job.source_id)
    if source is None:
        raise ProfilingUnavailable("The job's source does not exist")
    if job.status in (JobStatus.CREATED, JobStatus.UPLOADED):
        if source.kind is not SourceKind.CSV:
            raise ProfilingUnavailable("Run discovery first")
        if job.status is JobStatus.CREATED:
            jobs.transition(job, JobStatus.UPLOADED)
        jobs.transition(job, JobStatus.DISCOVERING, message="Delimited text needs no analysis of its structure")
    elif job.status not in (JobStatus.DISCOVERING, JobStatus.PROFILED):
        raise ProfilingUnavailable(f"A job in state {job.status.value} cannot be profiled")

    try:
        with ExitStack() as stack:
            tables = _tables_for(session, job, source, stack)
            profile, quality = profile_and_check(tables, source.kind.value, chunk_size=chunk_size)
        outcome = ProfilingOutcome(profile, quality)
        persist(session, job, source, outcome)
        if job.status is JobStatus.DISCOVERING:
            jobs.transition(job, JobStatus.PROFILED, message="Profiling finished",
                            detail={"rows": profile.rows_total, "quality_score": quality.score, "issues": quality.issue_counts})  # fmt: skip
        return outcome
    except ProfilingUnavailable:
        raise
    except Exception as exc:
        try:
            jobs.transition(job, JobStatus.FAILED, message=f"Profiling failed: {type(exc).__name__}")
        except InvalidTransition:
            pass
        raise


def load_profile(session: Session, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = session.scalar(select(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job_id, DiscoveryEvidence.subject == RESULT_PROFILE))
    return row.raw_json if row else None


def load_quality(session: Session, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = session.scalar(select(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job_id, DiscoveryEvidence.subject == RESULT_QUALITY))
    return row.raw_json if row else None
