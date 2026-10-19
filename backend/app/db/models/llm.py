import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, IdMixin, TenantMixin
from app.db.models._types import str_enum
from app.db.models.enums import LlmCallStatus


class LlmCall(IdMixin, CreatedAtMixin, TenantMixin, Base):
    __tablename__ = "llm_calls"

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[LlmCallStatus] = mapped_column(str_enum(LlmCallStatus, "llm_call_status"), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class LlmCache(IdMixin, CreatedAtMixin, TenantMixin, Base):
    """Answers keyed by SHA-256 of the normalized request. Scoped per organization."""

    __tablename__ = "llm_cache"

    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (UniqueConstraint("org_id", "request_hash", name="uq_llm_cache_org_hash"),)
