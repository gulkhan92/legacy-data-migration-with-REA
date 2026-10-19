import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.models import (
    Condition,
    Customer,
    Encounter,
    Medication,
    Observation,
    Order,
    OrderItem,
    Patient,
    Product,
)

# Children first so foreign keys never block the delete.
WAREHOUSE_MODELS_DELETE_ORDER = (
    OrderItem,
    Order,
    Product,
    Customer,
    Observation,
    Medication,
    Condition,
    Encounter,
    Patient,
)


class WarehouseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def delete_by_job(self, job_id: uuid.UUID) -> dict[str, int]:
        """Rollback by job id. Returns the number of rows removed per table."""
        removed: dict[str, int] = {}
        for model in WAREHOUSE_MODELS_DELETE_ORDER:
            result = self.session.execute(delete(model).where(model.job_id == job_id))
            removed[model.__tablename__] = result.rowcount or 0
        return removed
