import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import EMBEDDING_DIMENSIONS, Base, CreatedAtMixin, IdMixin, TenantMixin

_ZERO_UUID = text("'00000000-0000-0000-0000-000000000000'::uuid")


def hnsw_index(table: str) -> Index:
    return Index(
        f"ix_{table}_embedding_hnsw",
        "embedding",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


class TargetSchema(IdMixin, CreatedAtMixin, Base):
    """A canonical target schema. org_id NULL means a built in schema visible to everyone."""

    __tablename__ = "target_schemas"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, server_default="1")
    description: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index(
            "uq_target_schemas_scope_name_version",
            func.coalesce(org_id, _ZERO_UUID),
            name,
            version,
            unique=True,
        ),
    )


class TargetField(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "target_fields"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    schema_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_schemas.id", ondelete="CASCADE"), nullable=False, index=True
    )
    table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    synonyms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    examples: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    pii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    __table_args__ = (
        UniqueConstraint("schema_id", "table_name", "name", name="uq_target_fields_schema_table_name"),
        hnsw_index("target_fields"),
    )


class MappingMemory(IdMixin, CreatedAtMixin, TenantMixin, Base):
    """Approved mappings reused on later jobs so repeated schemas cost zero tokens."""

    __tablename__ = "mapping_memory"

    source_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("target_fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transform_hint: Mapped[str | None] = mapped_column(Text)
    times_approved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    __table_args__ = (
        UniqueConstraint("org_id", "source_signature", "target_field_id", name="uq_mapping_memory_signature"),
        hnsw_index("mapping_memory"),
    )
