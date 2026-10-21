"""Profile a source and check its quality.

Two streaming passes over the source in chunks of `chunk_size` rows:
  1. profile: per column statistics, then semantic types, keys, foreign keys and PII
  2. quality: pandera checks driven by rules derived from the profile
"""

from collections import Counter

from app.profiling import pii, relations, semantic
from app.profiling.column_profile import ColumnAccumulator
from app.profiling.models import ProfileResult, QualityReport, TableProfile
from app.profiling.quality import derive_rules, run_quality
from app.profiling.readers import DEFAULT_CHUNK, TableSource

LIMITATIONS = [
    "Distinct values are counted exactly up to 200,000 per column, beyond that the count is a lower bound",
    "Required, range and key rules are inferred from the data until a target schema supplies them",
]


def profile_tables(tables: list[TableSource], source_kind: str, *, chunk_size: int = DEFAULT_CHUNK) -> tuple[ProfileResult, dict[tuple[str, str], Counter[object]]]:
    profiles: list[TableProfile] = []
    counters: dict[tuple[str, str], Counter[object]] = {}
    for source in tables:
        accumulators = {h.name: ColumnAccumulator(h.name, h) for h in source.columns}
        for chunk in source.chunks(chunk_size):
            for name, acc in accumulators.items():
                acc.update(chunk[name])
        columns = []
        for position, (name, acc) in enumerate(accumulators.items()):
            column = acc.finalize(source.name, position)
            semantic.apply_layout_hints(column)
            semantic.classify(column)
            if acc.native and acc.zero_count and column.semantic_type in ("date", "timestamp") and not column.null_markers:
                column.null_markers = [0]
            if column.null_markers:  # a legacy null marker is a missing value too
                marked = sum(count for value, count in acc.values.items() if _as_int(value) in column.null_markers)
                column.null_count += marked
                column.null_rate = round((column.null_count + column.blank_count) / column.rows, 5) if column.rows else 0.0
            counters[(source.name, name)] = acc.values
            columns.append(column)
        sequences = [c.name for c in columns if c.layout_semantic == "key" or (semantic.sequential(c) and c.inferred_type == "integer")]
        rows = columns[0].rows if columns else 0
        profiles.append(TableProfile(name=source.name, rows=rows, columns=columns, sequence_columns=sequences))

    result = ProfileResult(source_kind=source_kind, tables=profiles, chunk_size=chunk_size,
                           rows_total=sum(t.rows for t in profiles), limitations=list(LIMITATIONS))  # fmt: skip
    result.foreign_keys = relations.infer_foreign_keys(profiles, counters)
    for table in profiles:
        for column in table.columns:
            pii.apply_pii(column)
    return result, counters


def _as_int(value: object) -> int | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else None


def key_sets(result: ProfileResult, counters: dict[tuple[str, str], Counter[object]]) -> dict[str, set[int]]:
    """Values of every referenced key column, for orphan detection."""
    wanted = {fk.parent for fk in result.foreign_keys}
    sets: dict[str, set[int]] = {}
    for name in wanted:
        table, column = name.split(".", 1)
        sets[name] = {int(float(v)) for v in counters[(table, column)]}  # type: ignore[arg-type]
    return sets


def profile_and_check(tables: list[TableSource], source_kind: str, *, chunk_size: int = DEFAULT_CHUNK) -> tuple[ProfileResult, QualityReport]:
    result, counters = profile_tables(tables, source_kind, chunk_size=chunk_size)
    rules = derive_rules(result, key_sets(result, counters))
    report = run_quality(tables, rules, chunk_size=chunk_size)
    return result, report
