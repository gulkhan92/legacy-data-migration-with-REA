"""Legacy SQLite database discovery through REA's SQLite inspection.

REA commands used (see docs/architecture.md):
  inspect-sqlite-database FILE --schema-scope database          full schema: tables, columns, indexes, views, triggers
  inspect-sqlite-database FILE --table T --row-limit N --json   a bounded row sample of one ordinary table
"""

from pathlib import Path
from typing import Any

from app.discovery.models import ColumnInfo, IndexInfo, SourceSchema, TableInfo, TriggerInfo, ViewInfo
from app.discovery.rea_adapter import ReaRun, ReaRunner


def _cell(cell: dict[str, Any]) -> Any:
    """Convert one REA tagged value to a Python value. Integers arrive as decimal strings."""
    kind = cell.get("type")
    if kind == "null":
        return None
    value = cell.get("value")
    if kind == "integer":
        return int(value)
    if kind == "blob":
        return {"blob_hex": cell.get("bytes_hex", "")[:64]}
    return value


def parse_schema(run: ReaRun) -> SourceSchema:
    result = run.normalized
    schema = result.get("schema", {})
    tables = []
    for entry in schema.get("tables", []):
        columns = [
            ColumnInfo(
                name=c["name"],
                declared_type=(c.get("declared_type") or None),
                nullable=not c.get("not_null", False),
                primary_key_position=c.get("primary_key_position", 0),
                hidden=bool(c.get("hidden", 0)),
            )
            for c in entry.get("columns", [])
        ]
        tables.append(
            TableInfo(
                name=entry["name"],
                kind=entry.get("kind", "table"),
                sql=entry.get("sql"),
                without_rowid=bool(entry.get("without_rowid", False)),
                columns=columns,
            )
        )
    engine = result.get("engine") or {}
    return SourceSchema(
        engine=f"{engine.get('name', 'SQLite')} {engine.get('version', '')}".strip(),
        database_encoding=result.get("database_encoding"),
        tables=tables,
        views=[ViewInfo(name=v["name"], sql=v.get("sql")) for v in schema.get("views", [])],
        indexes=[IndexInfo(name=i["name"], table=i["table"], sql=i.get("sql")) for i in schema.get("indexes", [])],
        triggers=[TriggerInfo(name=t["name"], table=t["table"], sql=t.get("sql")) for t in schema.get("triggers", [])],
        limitations=list(result.get("limitations", [])),
    )


def parse_rows(run: ReaRun) -> tuple[list[dict[str, Any]], bool]:
    rows = run.normalized.get("rows") or {}
    names = rows.get("columns", [])
    sample = [{name: _cell(cell) for name, cell in zip(names, record, strict=False)} for record in rows.get("values", [])]
    return sample, bool(rows.get("truncated") or rows.get("more_rows"))


def discover_sqlite(runner: ReaRunner, path: Path, *, sample_rows: int = 5) -> tuple[SourceSchema, list[ReaRun]]:
    """Return the schema (with row samples) and every REA run that produced it."""
    runs: list[ReaRun] = []
    schema_run = runner.run("inspect-sqlite-database", path, ["--schema-scope", "database"])
    runs.append(schema_run)
    schema = parse_schema(schema_run)
    for table in schema.tables:
        if table.kind != "table" or not table.columns or sample_rows <= 0:
            continue
        run = runner.run("inspect-sqlite-database", path, ["--table", table.name, "--row-limit", str(sample_rows)])
        runs.append(run)
        table.sample_rows, table.sample_truncated = parse_rows(run)
    return schema, runs
