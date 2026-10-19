import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

from app.db.base import EMBEDDING_DIMENSIONS, Base, CreatedAtMixin, IdMixin, TenantMixin
from app.db.models._types import str_enum
from app.db.models.enums import JobStatus, MappingMethod, MappingStatus, SourceKind


def _fk(target: str, *, nullable: bool = False, ondelete: str | None = None) -> MappedColumn[Any]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        index=True,
    )


class Source(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[SourceKind] = mapped_column(str_enum(SourceKind, "source_kind"), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    is_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")


class Job(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "jobs"

    source_id: Mapped[uuid.UUID] = _fk("sources.id", ondelete="RESTRICT")
    target_schema_id: Mapped[uuid.UUID | None] = _fk(
        "target_schemas.id", nullable=True, ondelete="SET NULL"
    )
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"),
        nullable=False,
        server_default=JobStatus.CREATED.value,
        index=True,
    )
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    error_message: Mapped[str | None] = mapped_column(Text)
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6000")
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class JobEvent(CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = _fk("jobs.id", ondelete="CASCADE")
    from_status: Mapped[JobStatus | None] = mapped_column(str_enum(JobStatus, "job_event_from_status"))
    to_status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus, "job_event_to_status"), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class DiscoveryEvidence(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "discovery_evidence"

    job_id: Mapped[uuid.UUID] = _fk("jobs.id", ondelete="CASCADE")
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    command: Mapped[str] = mapped_column(String(255), nullable=False)
    rea_version: Mapped[str | None] = mapped_column(String(50))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class SourceColumn(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "source_columns"

    job_id: Mapped[uuid.UUID] = _fk("jobs.id", ondelete="CASCADE")
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(255))
    inferred_type: Mapped[str | None] = mapped_column(String(50))
    semantic_type: Mapped[str | None] = mapped_column(String(50))
    pattern_class: Mapped[str | None] = mapped_column(String(100))
    null_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    distinct_count: Mapped[int | None] = mapped_column(BigInteger)
    pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    discovery_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    __table_args__ = (
        Index("uq_source_columns_job_table_column", "job_id", "table_name", "column_name", unique=True),
        Index(
            "ix_source_columns_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Mapping(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "mappings"

    job_id: Mapped[uuid.UUID] = _fk("jobs.id", ondelete="CASCADE")
    source_column_id: Mapped[uuid.UUID] = _fk("source_columns.id", ondelete="CASCADE")
    target_field_id: Mapped[uuid.UUID | None] = _fk(
        "target_fields.id", nullable=True, ondelete="SET NULL"
    )
    method: Mapped[MappingMethod] = mapped_column(str_enum(MappingMethod, "mapping_method"), nullable=False)
    status: Mapped[MappingStatus] = mapped_column(
        str_enum(MappingStatus, "mapping_status"),
        nullable=False,
        server_default=MappingStatus.SUGGESTED.value,
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    similarity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    transform_hint: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, ondelete="SET NULL")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_mappings_job_source_column", "job_id", "source_column_id", unique=True),)


class TransformRule(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "transform_rules"

    job_id: Mapped[uuid.UUID] = _fk("jobs.id", ondelete="CASCADE")
    mapping_id: Mapped[uuid.UUID] = _fk("mappings.id", ondelete="CASCADE")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    sample_failure_count: Mapped[int | None] = mapped_column(Integer)


class QuarantinedRow(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "quarantined_rows"

    job_id: Mapped[uuid.UUID] = _fk("jobs.id", ondelete="CASCADE")
    target_table: Mapped[str] = mapped_column(String(100), nullable=False)
    source_row_number: Mapped[int | None] = mapped_column(BigInteger)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason_detail: Mapped[str | None] = mapped_column(Text)
    raw_row: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ReconciliationReport(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "reconciliation_reports"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    loaded_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quarantined_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quality_before: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    quality_after: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
