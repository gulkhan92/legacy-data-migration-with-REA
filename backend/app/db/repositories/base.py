import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import tenant_of

ModelT = TypeVar("ModelT")


class Repository(Generic[ModelT]):
    """Generic data access. Tenant filtering is done by Row Level Security, not by this class.

    `add` fills `org_id` from the session tenant when the model has one, so callers cannot
    accidentally write a row for another organization (the policy would reject it anyway).
    """

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def list(self, *, limit: int = 50, offset: int = 0, **filters: Any) -> Sequence[ModelT]:
        stmt = select(self.model).filter_by(**filters).limit(limit).offset(offset)
        order_col = getattr(self.model, "created_at", None)
        if order_col is not None:
            stmt = stmt.order_by(order_col.desc())
        return self.session.scalars(stmt).all()

    def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        return self.session.execute(stmt).scalar_one()

    def add(self, entity: ModelT) -> ModelT:
        if hasattr(entity, "org_id") and getattr(entity, "org_id", None) is None:
            tenant = tenant_of(self.session)
            if tenant is not None:
                entity.org_id = tenant  # type: ignore[attr-defined]
        self.session.add(entity)
        self.session.flush()
        return entity

    def delete(self, entity: ModelT) -> None:
        self.session.delete(entity)
        self.session.flush()
