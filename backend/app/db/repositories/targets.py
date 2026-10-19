import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select

from app.db.models import MappingMemory, SourceColumn, TargetField
from app.db.repositories.base import Repository


class TargetFieldRepository(Repository[TargetField]):
    model = TargetField

    def nearest(
        self, embedding: Sequence[float], *, schema_id: uuid.UUID, limit: int = 3
    ) -> list[tuple[TargetField, float]]:
        """Top matches by cosine similarity (1.0 is identical), using the HNSW index."""
        distance = TargetField.embedding.cosine_distance(embedding)
        stmt = (
            select(TargetField, distance.label("distance"))
            .where(TargetField.schema_id == schema_id, TargetField.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        return [(row[0], 1.0 - float(row[1])) for row in self.session.execute(stmt)]


class MappingMemoryRepository(Repository[MappingMemory]):
    model = MappingMemory

    def nearest(
        self, embedding: Sequence[float], *, limit: int = 3
    ) -> list[tuple[MappingMemory, float]]:
        distance = MappingMemory.embedding.cosine_distance(embedding)
        stmt = (
            select(MappingMemory, distance.label("distance"))
            .where(MappingMemory.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        )
        return [(row[0], 1.0 - float(row[1])) for row in self.session.execute(stmt)]


class SourceColumnRepository(Repository[SourceColumn]):
    model = SourceColumn

    def for_job(self, job_id: uuid.UUID, **filters: Any) -> Sequence[SourceColumn]:
        stmt = (
            select(SourceColumn)
            .where(SourceColumn.job_id == job_id)
            .filter_by(**filters)
            .order_by(SourceColumn.table_name, SourceColumn.column_name)
        )
        return self.session.scalars(stmt).all()
