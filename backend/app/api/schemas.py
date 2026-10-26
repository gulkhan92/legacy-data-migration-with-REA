import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import Role, SourceKind


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    org_id: uuid.UUID | None = None


class MeResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    org_id: uuid.UUID
    org_name: str
    role: Role


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: SourceKind
    original_filename: str | None
    size_bytes: int | None
    sha256: str | None
    is_sample: bool
    created_at: datetime


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str | None = None
    action: str
    entity_type: str | None
    entity_id: str | None
    detail: dict[str, Any]
    created_at: datetime
