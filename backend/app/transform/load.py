"""Load: stream the source through the plan into a staging table with COPY, validate, and move the valid rows into the warehouse.

All of it runs inside the caller's transaction (a savepoint, see service.py): either every valid row of every table is in the
warehouse and every invalid row is in quarantine, or nothing is. The staging tables are temporary and vanish with the transaction.

For each target table (parents first):
  1. COPY every source row, transformed, into `stg_<table>`, with the reason found while transforming (see engine.py), a hash
     of the row's content and the source row as JSON.
  2. Validate in the database: references that point at no loaded parent (orphan_fk, which also catches parents that were
     themselves quarantined), a legacy key that appears twice with different content (duplicate_key), rows with the same content
     as an earlier row (duplicate).
  3. INSERT the valid rows into the warehouse (types cast from text, references resolved to the parent's id) and the invalid
     rows into quarantined_rows with their reason code.

While staging, a few facts about every row are kept in memory for the reconciliation: which rows were seen, which values are empty,
the numbers, the keys, and a deterministic sample of whole rows. They are independent of what the database did.
"""

import hashlib
import uuid
import zlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.profiling.readers import TableSource
from app.transform.engine import RuleStats, json_safe, to_text, transform_chunk
from app.transform.plan import RuleSpec, TablePlan, warehouse_table

SAMPLE_MODULUS = 20  # one row in twenty, chosen by the hash of its legacy key
SAMPLE_MAX = 400
FIELD_SEPARATOR = "\x1f"


@dataclass
class StageAggregates:
    """What the staging pass saw, kept in memory for the reconciliation."""

    source_rows: int = 0
    row_ids: list[np.ndarray] = field(default_factory=list)
    nulls: dict[str, list[np.ndarray]] = field(default_factory=dict)
    numbers: dict[str, list[list[Any]]] = field(default_factory=dict)  # per numeric field, the values in row order
    keys: list[Any] = field(default_factory=list)
    sample: dict[int, tuple[str | None, ...]] = field(default_factory=dict)  # row id -> canonical texts, in field order
    python_reasons: dict[str, int] = field(default_factory=dict)
    rule_stats: dict[str, RuleStats] = field(default_factory=dict)


@dataclass
class MoveResult:
    loaded: int = 0
    quarantined: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)


def ident(name: str) -> str:
    if not name.replace("_", "").isalnum() or not (name[0].isalpha() or name[0] == "_"):
        raise ValueError(f"unsafe identifier {name!r}")
    return '"' + name + '"'


def stage_name(table: str) -> str:
    return ident(f"stg_{table}")


def create_staging(session: Session, plan: TablePlan) -> None:
    columns = ", ".join(f"{ident(f)} text" for f in plan.fields if f != "source_key")
    session.execute(
        text(
            f"CREATE TEMP TABLE {stage_name(plan.target_table)} (row_id bigint NOT NULL, source_key text, dedupe text, reason text, detail text, "
            f"raw jsonb NOT NULL{', ' + columns if columns else ''}) ON COMMIT DROP"
        )
    )


def _copy_columns(plan: TablePlan) -> list[str]:
    return ["row_id", "source_key", "dedupe", "reason", "detail", "raw", *[f for f in plan.fields if f != "source_key"]]


def stage_table(session: Session, plan: TablePlan, source: TableSource, chunk_size: int) -> StageAggregates:
    """Transform the whole source table and COPY it into the staging table."""
    agg = StageAggregates()
    numeric = [r for r in plan.rules if r.numeric]
    for rule in numeric:
        agg.numbers[rule.target_field] = []
    for rule in plan.rules:
        agg.nulls[rule.target_field] = []
    value_fields = [f for f in plan.fields if f != "source_key"]
    business = plan.business_fields
    columns = _copy_columns(plan)
    cursor = session.connection().connection.driver_connection.cursor()  # type: ignore[union-attr]
    with cursor.copy(f"COPY {stage_name(plan.target_table)} ({', '.join(ident(c) for c in columns)}) FROM STDIN") as copy:
        for chunk in source.chunks(chunk_size):
            result = transform_chunk(plan, chunk)
            agg.source_rows += len(chunk)
            ids = np.asarray(chunk.index, dtype=np.int64)
            agg.row_ids.append(ids)
            for name, stats in result.stats.items():
                agg.rule_stats.setdefault(name, RuleStats()).add(stats)
            for name in agg.nulls:
                agg.nulls[name].append(result.values[name].map(lambda v: v is None).to_numpy(dtype=bool))
            for name in agg.numbers:
                agg.numbers[name].append(result.values[name].tolist())
            raws = json_safe(chunk)
            texts = {name: [to_text(v) for v in result.values[name].tolist()] for name in plan.fields}
            keys = texts.get("source_key", [None] * len(chunk))
            agg.keys.extend(keys)
            for i, row_id in enumerate(ids.tolist()):
                reason = result.reasons.iat[i]
                if reason is not None:
                    agg.python_reasons[reason] = agg.python_reasons.get(reason, 0) + 1
                content = FIELD_SEPARATOR.join("" if texts[f][i] is None else "1" + texts[f][i] for f in business)  # type: ignore[operator]
                dedupe = hashlib.md5(content.encode()).hexdigest()  # noqa: S324 (a content fingerprint, not a secret)
                copy.write_row((row_id, keys[i], dedupe, reason, result.details.iat[i], raws[i], *[texts[f][i] for f in value_fields]))
                key = keys[i]
                if key is not None and len(agg.sample) < SAMPLE_MAX and zlib.crc32(key.encode()) % SAMPLE_MODULUS == 0:
                    agg.sample[row_id] = tuple(texts[f][i] for f in plan.fields)
    return agg


# When more than this share of rows repeat another row's content, the content is not an identity (plain names and prices); only keys decide.
CONTENT_IDENTIFIES_BELOW = 0.1
# A row with only a few plain fields (a name, a category, a price) is not identified by them, however rarely they happen to repeat.
MIN_FIELDS_TO_IDENTIFY = 5


def _append_reason(table: str, where: str, reason: str, detail_sql: str) -> str:
    return f"UPDATE {table} s SET reason = '{reason}', detail = {detail_sql} {where}"


def validate_and_move(session: Session, plan: TablePlan, org_id: uuid.UUID, job_id: uuid.UUID) -> MoveResult:
    stg = stage_name(plan.target_table)
    params = {"job": job_id, "org": org_id}

    # 1. references that point at nothing: no such legacy key, or the parent was quarantined
    for rule in plan.rules:
        if rule.parent_table:
            parent = f"warehouse.{ident(rule.parent_table)}"
            session.execute(
                text(
                    f"UPDATE {stg} s SET reason = 'orphan_fk', detail = {_detail('s', f'{rule.target_field}: no loaded {rule.parent_table} has the key ')} || s.{ident(rule.target_field)} "
                    f"WHERE s.reason IS NULL AND s.{ident(rule.target_field)} IS NOT NULL AND NOT EXISTS "
                    f"(SELECT 1 FROM {parent} p WHERE p.job_id = :job AND p.source_key = s.{ident(rule.target_field)})"
                ),
                params,
            )

    # 2. the same row repeated (same key and content); the same content under a new key when content identifies a row (few rows share it);
    #    then a legacy key that appears again with different content
    session.execute(
        text(
            f"UPDATE {stg} s SET reason = 'duplicate', detail = 'same content as row ' || d.first_id "
            f"FROM (SELECT row_id, row_number() OVER w AS rn, first_value(row_id) OVER w AS first_id FROM {stg} WHERE reason IS NULL "
            f"WINDOW w AS (PARTITION BY source_key, dedupe ORDER BY row_id)) d WHERE s.row_id = d.row_id AND d.rn > 1 AND s.reason IS NULL"
        )
    )
    repeated = session.execute(
        text(f"SELECT count(*) FROM (SELECT row_number() OVER (PARTITION BY dedupe ORDER BY row_id) AS rn FROM {stg} WHERE reason IS NULL) t WHERE rn > 1")
    ).scalar_one()
    live = session.execute(text(f"SELECT count(*) FROM {stg} WHERE reason IS NULL")).scalar_one()
    if live and len(plan.business_fields) >= MIN_FIELDS_TO_IDENTIFY and repeated / live < CONTENT_IDENTIFIES_BELOW:
        session.execute(
            text(
                f"UPDATE {stg} s SET reason = 'duplicate', detail = 'same content as row ' || d.first_id || ' under another key' "
                f"FROM (SELECT row_id, row_number() OVER w AS rn, first_value(row_id) OVER w AS first_id FROM {stg} WHERE reason IS NULL "
                f"WINDOW w AS (PARTITION BY dedupe ORDER BY row_id)) d WHERE s.row_id = d.row_id AND d.rn > 1 AND s.reason IS NULL"
            )
        )
    session.execute(
        text(
            f"UPDATE {stg} s SET reason = 'duplicate_key', detail = 'source_key ' || s.source_key || ' appears again with different content (first at row ' || d.first_id || ')' "
            f"FROM (SELECT row_id, row_number() OVER w AS rn, first_value(row_id) OVER w AS first_id FROM {stg} WHERE reason IS NULL "
            f"WINDOW w AS (PARTITION BY source_key ORDER BY row_id)) d WHERE s.row_id = d.row_id AND d.rn > 1 AND s.reason IS NULL"
        )
    )

    # 3. valid rows into the warehouse
    wt = warehouse_table(plan.target_table)
    insert_cols = ["id", "org_id", "job_id", "source_key"]
    select_exprs = ["gen_random_uuid()", ":org", ":job", "s.source_key"]
    joins: list[str] = []
    for n, rule in enumerate(r for r in plan.rules if r.target_field != "source_key"):
        insert_cols.append(rule.target_field)
        if rule.parent_table:
            alias = f"p{n}"
            joins.append(f"LEFT JOIN warehouse.{ident(rule.parent_table)} {alias} ON {alias}.job_id = :job AND {alias}.source_key = s.{ident(rule.target_field)}")
            select_exprs.append(f"{alias}.id")
        else:
            sql_type = wt.columns[rule.target_field].type.compile(dialect=postgresql.dialect())
            is_text = sql_type.startswith(("VARCHAR", "TEXT"))
            select_exprs.append(f"s.{ident(rule.target_field)}" if is_text else f"CAST(s.{ident(rule.target_field)} AS {sql_type})")
    session.execute(
        text(
            f"INSERT INTO warehouse.{ident(plan.target_table)} ({', '.join(ident(c) for c in insert_cols)}) "
            f"SELECT {', '.join(select_exprs)} FROM {stg} s {' '.join(joins)} WHERE s.reason IS NULL ORDER BY s.row_id"
        ),
        params,
    )

    # 4. invalid rows into quarantine
    session.execute(
        text(
            f"INSERT INTO quarantined_rows (id, org_id, job_id, target_table, source_row_number, reason_code, reason_detail, raw_row) "
            f"SELECT gen_random_uuid(), :org, :job, '{plan.target_table}', s.row_id, s.reason, s.detail, s.raw FROM {stg} s WHERE s.reason IS NOT NULL ORDER BY s.row_id"
        ),
        params,
    )
    rows = session.execute(text(f"SELECT reason, count(*) FROM {stg} GROUP BY reason")).all()
    result = MoveResult()
    for reason, count in rows:
        if reason is None:
            result.loaded = count
        else:
            result.quarantined += count
            result.by_reason[reason] = count
    return result


def _detail(alias: str, literal: str) -> str:
    return "'" + literal.replace("'", "''") + "'"


def loaded_row_ids(session: Session, plan: TablePlan) -> np.ndarray:
    rows = session.execute(text(f"SELECT row_id FROM {stage_name(plan.target_table)} WHERE reason IS NULL ORDER BY row_id")).all()
    return np.asarray([r[0] for r in rows], dtype=np.int64)


def quarantined_keys(session: Session, plan: TablePlan) -> list[str | None]:
    return [r[0] for r in session.execute(text(f"SELECT source_key FROM {stage_name(plan.target_table)} WHERE reason IS NOT NULL"))]


def distinct_staged_keys(session: Session, plan: TablePlan) -> tuple[int, int]:
    """(distinct keys among loaded rows, distinct keys among quarantined rows that were not loaded)."""
    stg = stage_name(plan.target_table)
    loaded = session.scalar(text(f"SELECT count(DISTINCT source_key) FROM {stg} WHERE reason IS NULL"))
    only = session.scalar(
        text(f"SELECT count(DISTINCT source_key) FROM {stg} q WHERE q.reason IS NOT NULL AND q.source_key IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {stg} l WHERE l.reason IS NULL AND l.source_key = q.source_key)")
    )
    return int(loaded or 0), int(only or 0)


def decimals(values: list[Any]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]


def table_frame(source: TableSource, columns: list[str]) -> pd.DataFrame:  # pragma: no cover - convenience for ad hoc use
    return pd.concat(list(source.chunks(50_000)))[columns]
