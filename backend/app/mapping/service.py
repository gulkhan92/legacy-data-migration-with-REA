"""Map the columns of a profiled job to a target schema and keep what analysts approve.

Retrieval uses pgvector: the embedding of every column is compared with the stored embeddings of the target schema's
fields (cosine distance, HNSW index) and with the organization's approved mappings. The decision itself is the pure
function in decide.py.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.job_state import InvalidTransition
from app.db.models import Job, Mapping, MappingMemory, SourceColumn, TargetField, TargetSchema
from app.db.models.enums import JobStatus, MappingMethod, MappingStatus
from app.db.repositories.jobs import JobRepository
from app.db.repositories.targets import TargetFieldRepository
from app.mapping import llm_hook, texts
from app.mapping.compat import compatible
from app.mapping.decide import AUTO, MEMORY, NEEDS_LLM, REVIEW, UNMAPPED, ColumnInput, Decision, MemoryHit, TargetInfo, decide
from app.mapping.embedding import EmbeddingService, get_embedding_service
from app.mapping.thresholds import MappingThresholds, load_thresholds
from app.profiling.models import ColumnProfile
from app.profiling.vocabulary import expanded_name


class MappingUnavailable(Exception):
    """The job cannot be mapped yet (not profiled, unknown target schema, schema not embedded)."""


@dataclass
class MappingOutcome:
    decisions: list[Decision]
    counts: dict[str, int]
    schema_id: uuid.UUID
    llm_requests: int = 0
    llm_tokens: int = 0


def source_signature(table: str, p: ColumnProfile) -> str:
    """Identity of a legacy column across jobs: its expanded names, semantic type, type and pattern."""
    parts = [expanded_name(table), p.expanded_name or p.name.lower(), p.semantic_type or "", p.inferred_type, p.pattern_class or ""]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _profile_of(row: SourceColumn) -> ColumnProfile | None:
    stats = (row.profile or {}).get("stats")
    return ColumnProfile.model_validate(stats) if stats else None


def find_schema(session: Session, name: str) -> TargetSchema | None:
    rows = session.scalars(select(TargetSchema).where(TargetSchema.name == name)).all()
    # An organization's own schema wins over a built in one of the same name; the highest version wins.
    return max(rows, key=lambda r: (r.org_id is not None, r.version), default=None)


def _memory_hits(session: Session, schema_id: uuid.UUID, vector: np.ndarray, fields: dict[uuid.UUID, TargetField], p: ColumnProfile, t: MappingThresholds) -> MemoryHit | None:
    distance = MappingMemory.embedding.cosine_distance(vector)
    stmt = (
        select(MappingMemory.target_field_id, distance.label("distance"))
        .join(TargetField, TargetField.id == MappingMemory.target_field_id)
        .where(TargetField.schema_id == schema_id, MappingMemory.embedding.is_not(None))
        .order_by(distance)
        .limit(3)
    )
    for target_id, dist in session.execute(stmt):
        field = fields.get(target_id)
        if field is not None and dist <= t.memory_distance and compatible(p, field.data_type):
            return MemoryHit(f"{field.table_name}.{field.name}", float(dist))
    return None


def run_mapping(
    session: Session,
    job: Job,
    schema_name: str,
    *,
    embedder: EmbeddingService | None = None,
    resolver: llm_hook.Resolver | None = None,
    thresholds: MappingThresholds | None = None,
) -> MappingOutcome:
    t = thresholds or load_thresholds()
    embedder = embedder or get_embedding_service()
    jobs = JobRepository(session)
    if job.status not in (JobStatus.PROFILED, JobStatus.MAPPED):
        raise MappingUnavailable(f"A job in state {job.status.value} cannot be mapped; profile it first")
    schema = find_schema(session, schema_name)
    if schema is None:
        raise MappingUnavailable(f"Unknown target schema: {schema_name}")
    fields = list(session.scalars(select(TargetField).where(TargetField.schema_id == schema.id).order_by(TargetField.table_name, TargetField.name)))
    if not fields or any(f.embedding is None for f in fields):
        raise MappingUnavailable("The target schema has not been embedded; run `python -m app.mapping.seed_targets`")
    targets = [TargetInfo(f"{f.table_name}.{f.name}", f.table_name, f.name, f.data_type) for f in fields]
    by_id = {f.id: f for f in fields}
    index_of = {tg.key: i for i, tg in enumerate(targets)}

    rows = list(session.scalars(select(SourceColumn).where(SourceColumn.job_id == job.id).order_by(SourceColumn.table_name, SourceColumn.column_name)))
    profiled = [(r, p) for r in rows if (p := _profile_of(r)) is not None]
    if not profiled:
        raise MappingUnavailable("The job has not been profiled")

    # Embed every column in one batch, keep the vector for pgvector, memory and later approval.
    vectors = embedder.encode([texts.QUERY_PREFIX + texts.source_text(r.table_name, p, note=(r.profile or {}).get("note")) for r, p in profiled])
    field_repo = TargetFieldRepository(session)
    columns: list[ColumnInput] = []
    for (row, p), vector in zip(profiled, vectors, strict=True):
        row.embedding = vector
        sims = np.zeros(len(targets), dtype=np.float32)
        for field, similarity in field_repo.nearest(vector, schema_id=schema.id, limit=len(targets)):
            sims[index_of[f"{field.table_name}.{field.name}"]] = similarity
        memory = _memory_hits(session, schema.id, vector, by_id, p, t)
        columns.append(ColumnInput(row.table_name, p, sims, memory))
    session.flush()

    decisions = decide(columns, targets, t)
    requests = tokens = 0
    if resolver is not None:
        profiles = {c.key: c.profile for c in columns}
        info = {f"{f.table_name}.{f.name}": {"type": f.data_type, "description": f.description} for f in fields}
        requests, tokens = llm_hook.resolve(decisions, profiles, info, resolver, t)

    persist(session, job, schema, {(r.table_name, r.column_name): r for r, _ in profiled}, decisions, {f"{f.table_name}.{f.name}": f for f in fields})
    job.target_schema_id = schema.id
    counts = {status: sum(1 for d in decisions if d.status == status) for status in (MEMORY, AUTO, NEEDS_LLM, REVIEW, UNMAPPED)}
    if job.status is JobStatus.PROFILED:
        jobs.transition(job, JobStatus.MAPPED, message=f"Mapped to {schema_name}", detail={"counts": counts, "llm_requests": requests})
    return MappingOutcome(decisions, counts, schema.id, requests, tokens)


def persist(session: Session, job: Job, schema: TargetSchema, rows: dict[tuple[str, str], SourceColumn], decisions: list[Decision], fields: dict[str, TargetField]) -> None:
    session.execute(delete(Mapping).where(Mapping.job_id == job.id))
    for d in decisions:
        row = rows[(d.table, d.column)]
        target = fields.get(d.target) if d.target else None
        session.add(
            Mapping(
                org_id=job.org_id, job_id=job.id, source_column_id=row.id, target_field_id=target.id if target else None,
                method=MappingMethod(d.method), status=MappingStatus.SUGGESTED, decision=d.status,
                confidence=d.confidence, similarity=d.similarity, margin=d.margin,
                candidates=[{"field": key, "similarity": sim} for key, sim in d.candidates], rationale=d.reason,
            )
        )  # fmt: skip
    session.flush()


# ------------------------------------------------------------------ approval and memory


def approve_mapping(session: Session, mapping: Mapping, user_id: uuid.UUID, *, target_field_id: uuid.UUID | None = None) -> MappingMemory | None:
    """Approve (or override) one mapping and remember it. Returns the memory row, None when there is no target to remember."""
    now = datetime.now(UTC)
    if target_field_id is not None and target_field_id != mapping.target_field_id:
        mapping.target_field_id, mapping.status, mapping.method = target_field_id, MappingStatus.OVERRIDDEN, MappingMethod.HUMAN
    else:
        mapping.status = MappingStatus.APPROVED
    mapping.reviewed_by, mapping.reviewed_at = user_id, now
    if mapping.target_field_id is None:
        return None
    column = session.get(SourceColumn, mapping.source_column_id)
    profile = _profile_of(column) if column else None
    if column is None or profile is None or column.embedding is None:
        return None
    signature = source_signature(column.table_name, profile)
    memory = session.scalar(select(MappingMemory).where(MappingMemory.source_signature == signature, MappingMemory.target_field_id == mapping.target_field_id))
    text = texts.source_text(column.table_name, profile, note=(column.profile or {}).get("note"))
    if memory is None:
        memory = MappingMemory(org_id=mapping.org_id, source_signature=signature, source_text=text, target_field_id=mapping.target_field_id,
                               transform_hint=mapping.transform_hint, embedding=column.embedding)  # fmt: skip
        session.add(memory)
    else:
        memory.times_approved += 1
        memory.last_approved_at = now
        memory.source_text, memory.embedding = text, column.embedding
    session.flush()
    return memory


def approve_decided(session: Session, job: Job, user_id: uuid.UUID) -> int:
    """Approve every mapping the engine settled on (memory, auto, llm) in one go. Returns how many were approved."""
    count = 0
    for mapping in session.scalars(select(Mapping).where(Mapping.job_id == job.id, Mapping.decision.in_([MEMORY, AUTO]), Mapping.status == MappingStatus.SUGGESTED)):
        approve_mapping(session, mapping, user_id)
        count += 1
    return count


def mappings_for_job(session: Session, job_id: uuid.UUID) -> list[dict[str, Any]]:
    stmt = (
        select(Mapping, SourceColumn, TargetField)
        .join(SourceColumn, SourceColumn.id == Mapping.source_column_id)
        .outerjoin(TargetField, TargetField.id == Mapping.target_field_id)
        .where(Mapping.job_id == job_id)
        .order_by(SourceColumn.table_name, SourceColumn.column_name)
    )
    return [
        {
            "id": str(m.id), "source_table": c.table_name, "source_column": c.column_name,
            "target": f"{f.table_name}.{f.name}" if f else None, "method": m.method.value, "status": m.status.value,
            "decision": m.decision, "confidence": float(m.confidence) if m.confidence is not None else None,
            "similarity": float(m.similarity) if m.similarity is not None else None, "margin": float(m.margin) if m.margin is not None else None,
            "candidates": m.candidates, "rationale": m.rationale, "pii": c.pii,
        }
        for m, c, f in session.execute(stmt)
    ]


__all__ = ["run_mapping", "approve_mapping", "approve_decided", "mappings_for_job", "MappingUnavailable", "UNMAPPED", "InvalidTransition"]
