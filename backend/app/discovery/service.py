"""Run discovery for a job and persist what was found, with the evidence behind every field."""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.job_state import InvalidTransition
from app.db.models import DiscoveryEvidence, Job, Source, SourceColumn
from app.db.models.enums import JobStatus, SourceKind
from app.db.repositories.jobs import JobRepository
from app.discovery import cil_layout as cil
from app.discovery.dotnet_discovery import BinaryDiscovery, discover_layout
from app.discovery.models import LayoutSpec, SourceSchema
from app.discovery.rea_adapter import ReaError, ReaRun, ReaRunner, decrypted_workspace, get_rea_runner
from app.discovery.sqlite_discovery import discover_sqlite

ASSEMBLY_EXTENSIONS = {".dll", ".exe"}
RESULT_SCHEMA = "result:schema"
RESULT_LAYOUT = "result:layout"


class DiscoveryUnsupported(Exception):
    pass


@dataclass
class DiscoveryOutcome:
    kind: str
    schema: SourceSchema | None = None
    layout: LayoutSpec | None = None
    evidence_count: int = 0

    @property
    def confidence(self) -> float | None:
        return self.layout.confidence if self.layout else (1.0 if self.schema else None)


def _is_assembly(source: Source) -> bool:
    return Path(source.original_filename or "").suffix.lower() in ASSEMBLY_EXTENSIONS


def _clear_previous(session: Session, job_id: uuid.UUID) -> None:
    session.execute(delete(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job_id))
    session.execute(delete(SourceColumn).where(SourceColumn.job_id == job_id))


def _store_runs(session: Session, job: Job, runs: list[ReaRun]) -> None:
    for number, run in enumerate(runs, start=1):
        session.add(
            DiscoveryEvidence(
                org_id=job.org_id, job_id=job.id, subject=f"command:{number}:{run.command}",
                command=run.command_line(), rea_version=run.rea_version, duration_ms=run.duration_ms, raw_json=run.result,
            )  # fmt: skip
        )


def _store_result(session: Session, job: Job, subject: str, command: str, rea_version: str | None, payload: dict[str, Any]) -> None:
    session.add(
        DiscoveryEvidence(org_id=job.org_id, job_id=job.id, subject=subject, command=command, rea_version=rea_version, raw_json=payload)
    )


def persist_schema(session: Session, job: Job, schema: SourceSchema, runs: list[ReaRun]) -> int:
    _store_runs(session, job, runs)
    version = runs[0].rea_version if runs else None
    _store_result(session, job, RESULT_SCHEMA, "inspect-sqlite-database", version, schema.model_dump(mode="json"))
    count = len(runs) + 1
    for table in schema.tables:
        if table.kind != "table":
            continue
        for position, column in enumerate(table.columns):
            declared = (column.declared_type or "").lower() or None
            session.add(
                SourceColumn(
                    org_id=job.org_id, job_id=job.id, table_name=table.name, column_name=column.name, inferred_type=declared,
                    discovery_confidence=1.0,
                    profile={"declared_type": column.declared_type, "nullable": column.nullable, "ordinal": position,
                             "primary_key_position": column.primary_key_position},
                )  # fmt: skip
            )
            session.add(
                DiscoveryEvidence(
                    org_id=job.org_id, job_id=job.id, subject=f"{table.name}.{column.name}", command="inspect-sqlite-database",
                    rea_version=version, raw_json={"kind": "sqlite_column", "table": table.name, "column": column.model_dump(mode="json"),
                                                   "sample": [row.get(column.name) for row in table.sample_rows]},
                )  # fmt: skip
            )
            count += 1
    return count


def persist_layout(session: Session, job: Job, discovery: BinaryDiscovery) -> int:
    layout = discovery.layout
    _store_runs(session, job, discovery.runs)
    version = discovery.runs[0].rea_version if discovery.runs else None
    command = "inspect-managed-members" if layout.method == "cil" else "statistical-inference"
    _store_result(session, job, RESULT_LAYOUT, command, version, layout.model_dump(mode="json"))
    count = len(discovery.runs) + 1
    for table in layout.tables:
        for f in table.fields:
            key = f"{table.name}.{f.name}"
            session.add(
                SourceColumn(
                    org_id=job.org_id, job_id=job.id, table_name=table.name, column_name=f.name, inferred_type=f.type,
                    semantic_type=f.semantic, discovery_confidence=f.confidence,
                    profile={"offset": f.offset, "length": f.length, "endianness": f.endianness, "encoding": f.encoding,
                             "padding": f.padding, "source_hint": f.source_hint, "name_source": f.name_source, "note": f.note,
                             "record_size": table.record_size, "record_count": table.record_count},
                )  # fmt: skip
            )
            payload = dict(discovery.field_evidence.get(key) or {"kind": "statistical", "note": "Inferred from byte statistics of the data file"})
            payload["field"] = f.model_dump(mode="json")
            session.add(
                DiscoveryEvidence(org_id=job.org_id, job_id=job.id, subject=key, command=command, rea_version=version, raw_json=payload)
            )
            count += 1
    if discovery.rejected_layout is not None:
        _store_result(session, job, "result:rejected_layout", "inspect-managed-members", version,
                      discovery.rejected_layout.model_dump(mode="json"))  # fmt: skip
        count += 1
    return count


def run_discovery(
    session: Session,
    job: Job,
    *,
    runner: ReaRunner | None = None,
    resolver: cil.Resolver | None = None,
    sample_rows: int | None = None,
) -> DiscoveryOutcome:
    """Discover the structure of the job's source. Moves the job UPLOADED -> DISCOVERING (or to FAILED)."""
    from app.core.config import get_settings

    jobs = JobRepository(session)
    runner = runner or get_rea_runner()
    source = session.get(Source, job.source_id)
    data_source = session.get(Source, job.data_source_id) if job.data_source_id else None
    if source is None:
        raise DiscoveryUnsupported("The job's source does not exist")
    if job.status is JobStatus.CREATED:
        jobs.transition(job, JobStatus.UPLOADED)
    if job.status is not JobStatus.DISCOVERING:  # a repeated run of the same job replaces its earlier results
        jobs.transition(job, JobStatus.DISCOVERING, message=f"Discovery of {source.kind.value} source")
    _clear_previous(session, job.id)

    try:
        if source.kind is SourceKind.SQLITE:
            with decrypted_workspace(source.storage_path or "", "source.sqlite") as path:
                schema, runs = discover_sqlite(runner, path, sample_rows=sample_rows if sample_rows is not None else get_settings().rea_sample_rows)
            outcome = DiscoveryOutcome("sqlite", schema=schema, evidence_count=persist_schema(session, job, schema, runs))
        elif source.kind is SourceKind.DOTNET_BINARY:
            assembly, data = (source, data_source) if _is_assembly(source) else (None, source)
            discovery = _discover_binary(runner, assembly, data, resolver)
            outcome = DiscoveryOutcome("dotnet_binary", layout=discovery.layout, evidence_count=persist_layout(session, job, discovery))
        else:
            raise DiscoveryUnsupported("Delimited text files are profiled directly and need no REA discovery")
    except (ReaError, DiscoveryUnsupported) as exc:
        message = exc.message if isinstance(exc, ReaError) else str(exc)
        try:
            jobs.transition(job, JobStatus.FAILED, message=message, detail={"error": getattr(exc, "kind", "unsupported")})
        except InvalidTransition:
            pass
        raise
    return outcome


def _discover_binary(runner: ReaRunner, assembly: Source | None, data: Source | None, resolver: cil.Resolver | None) -> BinaryDiscovery:
    from contextlib import ExitStack

    with ExitStack() as stack:
        assembly_path = stack.enter_context(decrypted_workspace(assembly.storage_path or "", "source.dll")) if assembly else None
        data_path = stack.enter_context(decrypted_workspace(data.storage_path or "", "source.cdb")) if data else None
        return discover_layout(runner, assembly_path, data_path, resolver=resolver)


def load_result(session: Session, job_id: uuid.UUID) -> dict[str, Any] | None:
    row = session.scalar(
        select(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job_id, DiscoveryEvidence.subject.in_([RESULT_SCHEMA, RESULT_LAYOUT]))
    )
    return row.raw_json if row else None


def field_evidence(session: Session, job_id: uuid.UUID, subject: str) -> list[DiscoveryEvidence]:
    stmt = select(DiscoveryEvidence).where(DiscoveryEvidence.job_id == job_id, DiscoveryEvidence.subject == subject)
    return list(session.scalars(stmt.order_by(DiscoveryEvidence.created_at)))
