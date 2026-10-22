"""The transform plan of a job: which source table feeds which target table, and the rules that fill each target field.

The plan is built from the stored `transform_rules` (generated, then possibly edited by a person), the target schema fields and
the profile of the source columns. Rules are parsed with the DSL every time, so a rule that is not valid never reaches a load.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import WAREHOUSE_SCHEMA, Base
from app.db.models import SourceColumn, TargetField, TransformRule
from app.profiling.models import ColumnProfile
from app.profiling.quality import derive_bounds
from app.transform.dsl import ParsedStep, RuleError, parse_steps

IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
NUMERIC_TARGETS = {"integer", "decimal"}
DERIVED_PREFIX = "@"  # an input that names another target field of the same table instead of a source column
SYSTEM_COLUMNS = {"id", "org_id", "job_id", "created_at", "source_key"}


class PlanError(Exception):
    """The job cannot be loaded as planned: a rule needs input, a required field has no source, a table feeds twice, ..."""


@dataclass
class RuleSpec:
    id: uuid.UUID | None
    target_table: str
    target_field: str
    target_type: str
    required: bool
    source_table: str | None
    inputs: list[str]
    steps: list[ParsedStep]
    raw_steps: list[dict[str, Any]]
    status: str
    origin: str
    bounds: tuple[float, float] | None = None
    parent_table: str | None = None  # for a reference field: the warehouse table it points at
    notes: list[str] = field(default_factory=list)
    max_length: int | None = None  # text longer than the warehouse column allows
    limit: float | None = None  # numbers at or beyond this do not fit the warehouse column (precision or integer width)

    @property
    def derived(self) -> bool:
        return any(i.startswith(DERIVED_PREFIX) for i in self.inputs)

    @property
    def is_key(self) -> bool:
        return self.target_field == "source_key"

    @property
    def numeric(self) -> bool:
        return self.target_type in NUMERIC_TARGETS and not self.is_key and self.parent_table is None


@dataclass
class TablePlan:
    target_table: str
    source_table: str
    rules: list[RuleSpec]

    @property
    def fields(self) -> list[str]:
        return [r.target_field for r in self.rules]

    @property
    def source_columns(self) -> list[str]:
        out: list[str] = []
        for r in self.rules:
            out.extend(c for c in r.inputs if not c.startswith(DERIVED_PREFIX) and c not in out)
        return out

    @property
    def business_fields(self) -> list[str]:
        """The fields that make two rows the same record: everything except the legacy key and derived values."""
        return [r.target_field for r in self.rules if not r.is_key and not r.derived]


@dataclass
class Plan:
    tables: list[TablePlan]  # parents before children
    issues: list[str] = field(default_factory=list)  # reasons the job cannot be loaded yet
    warnings: list[str] = field(default_factory=list)


def warehouse_table(name: str):  # type: ignore[no-untyped-def]
    return Base.metadata.tables[f"{WAREHOUSE_SCHEMA}.{name}"]


def column_limits(table: str, field_name: str) -> tuple[int | None, float | None]:
    """(maximum text length, absolute limit of a number) of a warehouse column."""
    from sqlalchemy import BigInteger, Integer, Numeric, SmallInteger, String

    column = warehouse_table(table).columns.get(field_name)
    if column is None:
        return None, None
    kind = column.type
    if isinstance(kind, String) and kind.length:
        return kind.length, None
    if isinstance(kind, Numeric) and kind.precision is not None:
        return None, float(10 ** (kind.precision - (kind.scale or 0)))
    if isinstance(kind, BigInteger):
        return None, float(2**63)
    if isinstance(kind, SmallInteger):
        return None, float(2**15)
    if isinstance(kind, Integer):
        return None, float(2**31)
    return None, None


def parent_of(table: str, field_name: str) -> str | None:
    column = warehouse_table(table).columns.get(field_name)
    if column is None:
        return None
    for fk in column.foreign_keys:
        if fk.column.table.schema == WAREHOUSE_SCHEMA:
            return fk.column.table.name
    return None


def _bounds(profile: ColumnProfile | None, rule: RuleSpec) -> tuple[float, float] | None:
    if profile is None or not rule.numeric:
        return None
    found = derive_bounds(profile)
    if found is None:
        return None
    scale = 0.01 if any(s.spec.name == "cents_to_decimal" for s in rule.steps) else 1.0
    return found[0] * scale, found[1] * scale


def build_plan(session: Session, job_id: uuid.UUID, schema_id: uuid.UUID) -> Plan:
    fields = {(f.table_name, f.name): f for f in session.scalars(select(TargetField).where(TargetField.schema_id == schema_id))}
    profiles: dict[tuple[str, str], ColumnProfile] = {}
    for row in session.scalars(select(SourceColumn).where(SourceColumn.job_id == job_id)):
        stats = (row.profile or {}).get("stats")
        if stats:
            profiles[(row.table_name, row.column_name)] = ColumnProfile.model_validate(stats)

    plan = Plan([])
    by_table: dict[str, list[RuleSpec]] = {}
    sources: dict[str, set[str]] = {}
    for rule in session.scalars(select(TransformRule).where(TransformRule.job_id == job_id).order_by(TransformRule.target_table, TransformRule.target_field)):
        target = fields.get((rule.target_table, rule.target_field))
        if target is None or not IDENTIFIER.match(rule.target_table) or not IDENTIFIER.match(rule.target_field):
            plan.issues.append(f"{rule.target_table}.{rule.target_field} is not a field of the target schema")
            continue
        if rule.status != "ready":
            plan.issues.append(f"The rule for {rule.target_table}.{rule.target_field} needs input ({rule.status}): {'; '.join(rule.notes) or 'see the rule'}")
            continue
        try:
            steps = parse_steps(rule.steps, len(rule.inputs))
        except RuleError as exc:
            plan.issues.append(f"The rule for {rule.target_table}.{rule.target_field} is not valid: {exc}")
            continue
        spec = RuleSpec(rule.id, rule.target_table, rule.target_field, target.data_type, target.required, rule.source_table, list(rule.inputs),
                        steps, list(rule.steps), rule.status, rule.origin, None, parent_of(rule.target_table, rule.target_field), list(rule.notes))  # fmt: skip
        if target.data_type == "reference" and spec.parent_table is None:
            plan.issues.append(f"{rule.target_table}.{rule.target_field} is a reference but the warehouse has no table for it to point at")
            continue
        primary = next((i for i in spec.inputs if not i.startswith(DERIVED_PREFIX)), None)
        spec.bounds = _bounds(profiles.get((rule.source_table or "", primary or "")), spec)
        spec.max_length, spec.limit = column_limits(rule.target_table, rule.target_field)
        by_table.setdefault(rule.target_table, []).append(spec)
        if rule.source_table:
            sources.setdefault(rule.target_table, set()).add(rule.source_table)

    tables: list[TablePlan] = []
    for target_table, rules in by_table.items():
        source_tables = sources.get(target_table, set())
        if len(source_tables) != 1:
            plan.issues.append(f"{target_table} is fed by {len(source_tables)} source tables ({', '.join(sorted(source_tables)) or 'none'}); exactly one is supported")
            continue
        names = {r.target_field for r in rules}
        if "source_key" not in names:
            plan.issues.append(f"{target_table} has no source_key rule: rows could not be matched back to the legacy system")
        for f in fields.values():
            if f.table_name == target_table and f.required and f.name not in names:
                plan.issues.append(f"{target_table}.{f.name} is required but no source column is mapped to it")
        ordered = sorted(rules, key=lambda r: (r.derived, not r.is_key, r.target_field))
        tables.append(TablePlan(target_table, next(iter(source_tables)), ordered))

    present = {t.target_table for t in tables}
    for t in tables:
        for r in t.rules:
            if r.parent_table and r.parent_table not in present:
                plan.issues.append(f"{t.target_table}.{r.target_field} refers to {r.parent_table}, which is not part of this load")
    plan.tables = order_by_dependency(tables)
    return plan


def order_by_dependency(tables: list[TablePlan]) -> list[TablePlan]:
    remaining = {t.target_table: t for t in tables}
    ordered: list[TablePlan] = []
    done: set[str] = set()
    while remaining:
        ready = [t for t in remaining.values() if all(r.parent_table is None or r.parent_table in done or r.parent_table not in remaining or r.parent_table == t.target_table
                                                      for r in t.rules)]  # fmt: skip
        if not ready:  # a cycle: keep the rest in a fixed order, the database will show if it matters
            ordered.extend(sorted(remaining.values(), key=lambda t: t.target_table))
            break
        for t in sorted(ready, key=lambda t: t.target_table):
            ordered.append(t)
            done.add(t.target_table)
            del remaining[t.target_table]
    return ordered
