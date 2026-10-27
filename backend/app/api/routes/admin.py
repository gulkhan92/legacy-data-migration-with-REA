"""Administration of one organization: its users and roles, and its audit log. Admin only."""

import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_tenant_db, require_role
from app.api.pagination import Page, PageParams
from app.api.schemas import AuditEntryOut
from app.core.security import Principal, hash_password, validate_password
from app.db.models import AuditLog, Lead, Membership, OrgSettings, RefreshToken, User
from app.db.models.enums import Role
from app.services.audit import AuditAction, record_audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    member_since: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"email": "analyst@example.com", "full_name": "Ada Analyst", "role": "analyst", "password": "Correct-Horse-9-Battery"}]})

    email: str = Field(max_length=320)
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    password: str = Field(max_length=256, description="The first password, which the person should change")

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if not EMAIL.match(value):
            raise ValueError("not an email address")
        return value


class RoleChange(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "viewer"}]})

    role: Role


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    email: str
    company: str | None
    message: str | None
    kind: str
    created_at: datetime


def _user_out(user: User, membership: Membership) -> UserOut:
    return UserOut(user_id=user.id, email=user.email, full_name=user.full_name, role=membership.role, is_active=user.is_active, member_since=membership.created_at)


def _admin_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Membership).where(Membership.role == Role.ADMIN)) or 0


def _membership(db: Session, user_id: uuid.UUID) -> tuple[User, Membership]:
    row = db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id).where(User.id == user_id)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return row[0], row[1]


@router.get("/audit-log", response_model=list[AuditEntryOut], summary="The organization's audit log, newest first, with search")
def audit_log(
    response: Response,
    action: str | None = None,
    q: str | None = Query(None, max_length=100, description="Part of the action, the entity, the person or the details"),
    user_id: uuid.UUID | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: Principal = Depends(require_role()),  # admin only
    db: Session = Depends(get_tenant_db, scope="function"),
) -> list[AuditEntryOut]:
    """The total number of matching entries is in the `X-Total-Count` header."""
    stmt = select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to:
        stmt = stmt.where(AuditLog.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC))
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(
            func.lower(AuditLog.action).like(like) | func.lower(func.coalesce(AuditLog.entity_type, "")).like(like) | func.lower(func.coalesce(AuditLog.entity_id, "")).like(like)
            | func.lower(func.coalesce(User.full_name, "")).like(like) | func.lower(cast(AuditLog.detail, Text)).like(like)
        )
    response.headers["X-Total-Count"] = str(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id).limit(limit).offset(offset)).all()
    return [AuditEntryOut.model_validate(entry).model_copy(update={"user_name": name}) for entry, name in rows]


class RetentionIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"retention_days": 90}]})

    retention_days: int = Field(ge=7, le=3650, description="Days an uploaded file's encrypted copy is kept after its jobs have finished")


@router.get("/retention", summary="How long uploaded files are kept")
def get_retention(principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    row = db.get(OrgSettings, principal.org_id)
    return {"retention_days": row.retention_days if row else 365, "applies_to": "The encrypted copy of an uploaded file. Migrated data, reports and the audit log are not touched."}


@router.put("/retention", summary="Change how long uploaded files are kept (admin)")
def put_retention(body: RetentionIn, request: Request, principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function")) -> dict[str, Any]:
    row = db.get(OrgSettings, principal.org_id)
    previous = row.retention_days if row else 365
    if row is None:
        row = OrgSettings(org_id=principal.org_id)
        db.add(row)
    row.retention_days, row.updated_by = body.retention_days, principal.user_id
    db.flush()
    record_audit(db, AuditAction.RETENTION_CHANGE, org_id=principal.org_id, user_id=principal.user_id, entity_type="retention", ip=client_ip(request), detail={"from": previous, "to": body.retention_days})
    return get_retention(principal, db)


@router.get("/users", response_model=Page[UserOut], summary="The users of the organization")
def list_users(
    role: Role | None = None, q: str | None = Query(None, max_length=100, description="Part of the name or email"), page: PageParams = Depends(),
    _: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function"),
) -> Page[UserOut]:
    stmt = select(User, Membership).join(Membership, Membership.user_id == User.id)
    if role:
        stmt = stmt.where(Membership.role == role)
    if q:
        like = f"%{q.lower().replace('%', '').replace('_', '')}%"
        stmt = stmt.where(func.lower(User.email).like(like) | func.lower(User.full_name).like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(User.email).limit(page.limit).offset(page.offset)).all()
    return Page[UserOut](items=[_user_out(u, m) for u, m in rows], total=int(total), limit=page.limit, offset=page.offset)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Add a user to the organization")
def create_user(body: UserCreate, request: Request, principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function")) -> UserOut:
    problems = validate_password(body.password, email=body.email)
    if problems:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The password " + ", ".join(problems))
    # The same answer whether the address belongs to this organization or another one: it does not reveal who has an account.
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This user cannot be created")
    user = User(email=body.email, password_hash=hash_password(body.password), full_name=body.full_name.strip())
    db.add(user)
    db.flush()
    membership = Membership(org_id=principal.org_id, user_id=user.id, role=body.role)
    db.add(membership)
    db.flush()
    record_audit(db, AuditAction.USER_CREATE, org_id=principal.org_id, user_id=principal.user_id, entity_type="user", entity_id=str(user.id), ip=client_ip(request), detail={"role": body.role.value})
    return _user_out(user, membership)


@router.patch("/users/{user_id}", response_model=UserOut, summary="Change the role of a user")
def change_role(user_id: uuid.UUID, body: RoleChange, request: Request, principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function")) -> UserOut:
    user, membership = _membership(db, user_id)
    if membership.role is Role.ADMIN and body.role is not Role.ADMIN and _admin_count(db) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The organization needs at least one admin")
    previous = membership.role
    membership.role = body.role
    db.flush()
    record_audit(db, AuditAction.USER_ROLE_CHANGE, org_id=principal.org_id, user_id=principal.user_id, entity_type="user", entity_id=str(user.id), ip=client_ip(request),
                 detail={"from": previous.value, "to": body.role.value})  # fmt: skip
    return _user_out(user, membership)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a user from the organization")
def remove_user(user_id: uuid.UUID, request: Request, principal: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function")) -> None:
    """Removes the membership and ends the user's sessions in this organization. The person's account itself stays, they may belong to others."""
    if user_id == principal.user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot remove yourself")
    user, membership = _membership(db, user_id)
    if membership.role is Role.ADMIN and _admin_count(db) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The organization needs at least one admin")
    db.delete(membership)
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.org_id == principal.org_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.now(UTC)))
    record_audit(db, AuditAction.USER_REMOVE, org_id=principal.org_id, user_id=principal.user_id, entity_type="user", entity_id=str(user.id), ip=client_ip(request))


@router.get("/leads", response_model=Page[LeadOut], summary="Contact and demo requests from the public site")
def list_leads(kind: str | None = Query(None, max_length=32), page: PageParams = Depends(), _: Principal = Depends(require_role()), db: Session = Depends(get_tenant_db, scope="function")) -> Page[LeadOut]:
    stmt = select(Lead)
    if kind:
        stmt = stmt.where(Lead.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Lead.created_at.desc()).limit(page.limit).offset(page.offset)).all()
    return Page[LeadOut](items=[LeadOut.model_validate(r) for r in rows], total=int(total), limit=page.limit, offset=page.offset)


__all__: list[Any] = ["router"]
