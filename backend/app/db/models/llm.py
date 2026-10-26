import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
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
    error_code: Mapped[str | None] = mapped_column(String(40))
    prompt_version: Mapped[str | None] = mapped_column(String(60))

    __table_args__ = (Index("ix_llm_calls_org_id_created_at", "org_id", "created_at"),)


class LlmCache(IdMixin, CreatedAtMixin, TenantMixin, Base):
    """Answers keyed by SHA-256 of the normalized request. Scoped per organization."""

    __tablename__ = "llm_cache"

    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(60))
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")  # what the original call cost
    hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("org_id", "request_hash", name="uq_llm_cache_org_hash"),)


class OrgSettings(CreatedAtMixin, Base):
    """Per organization settings. Today: the LLM provider order and the token and call budgets (API keys are never stored)."""

    __tablename__ = "org_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    llm_provider_order: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default='["groq", "gemini"]')
    llm_job_token_budget: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6000")
    llm_org_daily_token_budget: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100000")
    llm_max_calls_per_job: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    # How long the encrypted copy of an uploaded file is kept after its jobs have finished (a nightly job deletes older copies).
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="365")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
