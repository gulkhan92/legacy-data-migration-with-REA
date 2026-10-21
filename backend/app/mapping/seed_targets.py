"""Load the built in target schemas and embed their fields.

    uv run python -m app.mapping.seed_targets

Reads datasets/targets/*.json, stores each schema with `org_id` NULL (readable by every organization) and one row per
field with its 384 dimension embedding. Safe to run again: unchanged fields keep their rows, changed descriptions are
re-embedded. Run as the table owner, because tenants cannot write built in rows.
"""

import json
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import TargetField, TargetSchema
from app.mapping import texts
from app.mapping.embedding import EmbeddingService, get_embedding_service
from app.discovery.rea_adapter import REPO_ROOT

TARGETS_DIR = REPO_ROOT / "datasets" / "targets"


def load_schema_files(directory: Path = TARGETS_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*_target.json"))]


def seed(session: Session, schemas: list[dict] | None = None, service: EmbeddingService | None = None) -> dict[str, int]:
    """Create or refresh the built in schemas. Returns the number of fields embedded per schema."""
    service = service or get_embedding_service()
    embedded: dict[str, int] = {}
    for data in schemas if schemas is not None else load_schema_files():
        schema = session.scalar(select(TargetSchema).where(TargetSchema.org_id.is_(None), TargetSchema.name == data["schema"], TargetSchema.version == data["version"]))
        if schema is None:
            schema = TargetSchema(org_id=None, name=data["schema"], version=data["version"], description=data.get("description"))
            session.add(schema)
            session.flush()
        existing = {(f.table_name, f.name): f for f in session.scalars(select(TargetField).where(TargetField.schema_id == schema.id))}
        todo = []
        for field in data["fields"]:
            row = existing.get((field["table"], field["name"]))
            if row is None:
                row = TargetField(org_id=None, schema_id=schema.id, table_name=field["table"], name=field["name"], data_type=field["type"],
                                  description=field["description"])  # fmt: skip
                session.add(row)
            row.data_type, row.required, row.description = field["type"], field["required"], field["description"]
            row.synonyms, row.examples, row.pii = field["synonyms"], field["examples"], field["pii"]
            todo.append((row, texts.target_text(field)))
        vectors = service.encode([text for _, text in todo])
        for (row, _), vector in zip(todo, vectors, strict=True):
            row.embedding = vector
        embedded[data["schema"]] = len(todo)
    session.commit()
    return embedded


def main() -> None:
    engine = create_engine(get_settings().database_admin_url)
    with Session(engine) as session:
        for name, count in seed(session).items():
            print(f"{name}: {count} fields embedded")


if __name__ == "__main__":
    main()
