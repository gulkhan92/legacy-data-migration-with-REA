"""Create and edit the stored transform rules of a job."""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Job, Mapping, SourceColumn, TargetField, TransformRule
from app.db.models.enums import MappingStatus
from app.profiling.models import ColumnProfile
from app.transform import generate
from app.transform.dsl import RuleError, parse_steps
from app.transform.plan import DERIVED_PREFIX

APPLIED = (MappingStatus.APPROVED, MappingStatus.OVERRIDDEN)

# Target fields that no source column feeds but that follow from another field. (table, field) -> (base field, steps, note)
DERIVATIONS: dict[tuple[str, str], tuple[str, list[dict[str, Any]], str]] = {
    ("patients", "is_deceased"): ("death_date", [{"op": "is_not_null"}], "True when the patient has a death date"),
}


@dataclass
class GenerationResult:
    created: int = 0
    kept_human: int = 0
    needs_input: int = 0
    skipped_unapproved: int = 0
    conflicts: list[str] = field(default_factory=list)
    derived: int = 0


def _profile(row: SourceColumn) -> ColumnProfile | None:
    stats = (row.profile or {}).get("stats")
    return ColumnProfile.model_validate(stats) if stats else None


def generate_rules(session: Session, job: Job) -> GenerationResult:
    """Rules for every approved (or overridden) mapping. Rules a person wrote are kept as long as their target stays mapped."""
    result = GenerationResult()
    rows = session.execute(
        select(Mapping, SourceColumn, TargetField)
        .join(SourceColumn, SourceColumn.id == Mapping.source_column_id)
        .join(TargetField, TargetField.id == Mapping.target_field_id)
        .where(Mapping.job_id == job.id)
    ).all()
    result.skipped_unapproved = sum(1 for m, _, _ in rows if m.status not in APPLIED)
    chosen: dict[tuple[str, str], tuple[Mapping, SourceColumn, TargetField]] = {}
    for mapping, column, target in rows:
        if mapping.status not in APPLIED:
            continue
        key = (target.table_name, target.name)
        if key in chosen:
            keep, other = (chosen[key], (mapping, column, target)) if (chosen[key][0].confidence or 0) >= (mapping.confidence or 0) else ((mapping, column, target), chosen[key])
            result.conflicts.append(f"{target.table_name}.{target.name} is mapped from {chosen[key][1].column_name} and {column.column_name}; kept {keep[1].column_name}")
            chosen[key] = keep
            continue
        chosen[key] = (mapping, column, target)

    existing = {(r.target_table, r.target_field): r for r in session.scalars(select(TransformRule).where(TransformRule.job_id == job.id))}
    for key, rule in existing.items():
        if rule.origin == "human" and key in chosen:
            result.kept_human += 1
            continue
        session.execute(delete(TransformRule).where(TransformRule.id == rule.id))
    session.flush()

    ready_fields: set[tuple[str, str]] = set()
    for (table, name), (mapping, column, target) in chosen.items():
        old = existing.get((table, name))
        if old is not None and old.origin == "human":
            old.mapping_id, old.target_field_id, old.source_table, old.inputs = mapping.id, target.id, column.table_name, [column.column_name]
            if old.status == "ready":
                ready_fields.add((table, name))
            continue
        profile = _profile(column)
        if profile is None:
            result.conflicts.append(f"{column.table_name}.{column.column_name} has no profile; profile the job first")
            continue
        generated = generate.generate(profile, target.data_type, target.name, note=(column.profile or {}).get("note"), hint=mapping.transform_hint)
        _store(session, job, mapping, target, column.table_name, [column.column_name], generated)
        result.created += 1
        result.needs_input += generated.status != generate.READY
        if generated.status == generate.READY:
            ready_fields.add((table, name))

    schema_fields = {(f.table_name, f.name): f for f in session.scalars(select(TargetField).where(TargetField.schema_id == job.target_schema_id))}
    source_tables = {t: s for (t, _), (_, c, _) in chosen.items() for s in [c.table_name]}
    for (table, name), (base, steps, note) in DERIVATIONS.items():
        target = schema_fields.get((table, name))
        if target is None or (table, name) in chosen or (table, base) not in ready_fields:
            continue
        generated = generate.GeneratedRule(steps, generate.READY, [note], origin="derived")
        _store(session, job, None, target, source_tables[table], [DERIVED_PREFIX + base], generated)
        result.derived += 1
    session.flush()
    return result


def _store(session: Session, job: Job, mapping: Mapping | None, target: TargetField, source_table: str, inputs: list[str], rule: generate.GeneratedRule) -> TransformRule:
    row = TransformRule(
        org_id=job.org_id, job_id=job.id, mapping_id=mapping.id if mapping else None, target_field_id=target.id, target_table=target.table_name,
        target_field=target.name, source_table=source_table, inputs=inputs, steps=rule.steps, status=rule.status, origin=rule.origin,
        notes=rule.notes + ([f"Needs: {rule.missing}"] if rule.missing else []), failures={}, sample_failure_count=None,
    )  # fmt: skip
    if rule.missing:
        row.failures = {"missing": rule.missing}
    session.add(row)
    return row


class RuleRejected(ValueError):
    pass


def set_rule(session: Session, job: Job, rule: TransformRule, steps: list[dict[str, Any]]) -> TransformRule:
    """Replace a rule's steps by a person's. The steps are validated against the DSL whitelist; the caller then checks them on a
    sample before the rule is used."""
    try:
        parse_steps(steps, len(rule.inputs))
    except RuleError as exc:
        raise RuleRejected(str(exc)) from None
    rule.steps, rule.origin, rule.status, rule.failures, rule.sample_failure_count = steps, "human", "ready", {}, None
    rule.notes = [*[n for n in rule.notes if not n.startswith("Needs:")], "Set by a person"]
    session.flush()
    return rule


def find_rule(session: Session, job_id: uuid.UUID, rule_id: uuid.UUID) -> TransformRule | None:
    return session.scalar(select(TransformRule).where(TransformRule.id == rule_id, TransformRule.job_id == job_id))
