"""Read a job's loaded rows back from the warehouse in the shape of the target schema: references as the legacy key of the parent."""

import uuid
from collections.abc import Callable, Iterator

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.profiling.readers import ColumnHint, TableSource
from app.transform.load import ident
from app.transform.plan import TablePlan


def select_sql(plan: TablePlan, where: str = "") -> tuple[str, list[str]]:
    """SELECT for the loaded rows of a table with its fields in plan order. A reference field returns the parent's source_key."""
    columns: list[str] = []
    joins: list[str] = []
    for n, rule in enumerate(plan.rules):
        if rule.target_field == "source_key":
            columns.append(f't.source_key AS {ident("source_key")}')
        elif rule.parent_table:
            alias = f"p{n}"
            joins.append(f"LEFT JOIN warehouse.{ident(rule.parent_table)} {alias} ON {alias}.id = t.{ident(rule.target_field)}")
            columns.append(f"{alias}.source_key AS {ident(rule.target_field)}")
        else:
            columns.append(f"t.{ident(rule.target_field)} AS {ident(rule.target_field)}")
    sql = f"SELECT {', '.join(columns)} FROM warehouse.{ident(plan.target_table)} t {' '.join(joins)} WHERE t.job_id = :job {where} ORDER BY t.source_key"
    return sql, plan.fields


def read_rows(session: Session, plan: TablePlan, job_id: uuid.UUID, keys: list[str] | None = None) -> pd.DataFrame:
    where = "AND t.source_key = ANY(:keys)" if keys is not None else ""
    sql, names = select_sql(plan, where)
    params: dict[str, object] = {"job": job_id}
    if keys is not None:
        params["keys"] = keys
    rows = session.execute(text(sql), params).all()
    return pd.DataFrame(rows, columns=names, dtype=object)


def tables(session: Session, plans: list[TablePlan], job_id: uuid.UUID, chunk_size: int = 50_000) -> list[TableSource]:
    """The loaded warehouse tables as table sources, so profiling and the quality checks can run on what was loaded."""
    sources = []
    for plan in plans:
        sql, names = select_sql(plan)
        count = session.scalar(text(f"SELECT count(*) FROM warehouse.{ident(plan.target_table)} WHERE job_id = :job"), {"job": job_id}) or 0

        def chunks(size: int, sql: str = sql, names: list[str] = names) -> Iterator[pd.DataFrame]:
            result = session.connection().execute(text(sql).execution_options(stream_results=True), {"job": job_id})
            position = 0
            while rows := result.fetchmany(size):
                frame = pd.DataFrame(rows, columns=names, dtype=object)
                frame.index = pd.Index(range(position, position + len(frame)), name="row")
                position += len(frame)
                yield frame

        sources.append(TableSource(plan.target_table, [ColumnHint(n) for n in names], int(count), chunks))
    return sources


Reader = Callable[[], list[TableSource]]
