"""Data quality checks with pandera, and the data quality score.

Rules are derived from the profile (see `derive_rules`) and can be replaced by explicit rules once a target schema is
known (required flags, ranges, keys). Checks run chunk by chunk through a pandera DataFrameSchema per table;
duplicates and foreign keys need state across chunks and use hashes and key sets built by the profiling pass.

Issue types
  required_null  a value is missing where nearly every other row has one
  invalid_date   a date or timestamp that cannot be read, is not a real calendar date, or is out of 1800 to 2100
  out_of_range   a number far outside the range of its column (negative where the column is non negative)
  invalid_number a value in a numeric column that is not a number
  orphan_fk      a reference to a key that does not exist
  duplicate      a row identical to an earlier row (row numbers excluded)
  whitespace     (warning) leading or trailing spaces where almost no other value has them
  case_anomaly   (warning) a casing style that almost no other value in the column uses

Score (0 to 100), per table and for the whole source:
  blocking rows = rows with at least one required_null, invalid_date, out_of_range, invalid_number, orphan_fk or duplicate
  warning rows  = rows with only whitespace or case_anomaly issues
  table score   = 100 * (1 - min(1, (blocking rows + 0.25 * warning rows) / rows))
  source score  = row weighted mean of the table scores
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
import pandera.pandas as pa

from app.profiling.column_profile import DATE_FORMATS, NUMERIC_FORMATS, YEAR_MAX, YEAR_MIN, parse_numeric
from app.profiling.models import ColumnProfile, ProfileResult, QualityIssue, QualityReport, TableQuality
from app.profiling.readers import TableSource

BLOCKING = {"required_null", "invalid_date", "out_of_range", "invalid_number", "orphan_fk", "duplicate"}
WARNING_WEIGHT = 0.25
MAX_NULL_RATE = 0.06  # a column with more empty values than this is optional
MIN_ROWS_FOR_RULES = 20
RANGE_PAD = 10.0
NON_NEGATIVE_SHARE = 0.02
NO_RANGE_SEMANTICS = {"identifier", "reference", "flags", "date", "timestamp", "date_of_birth", "boolean"}
NO_REQUIRED_SEMANTICS = {"free_text", "flags"}
CASE_MINORITY = 0.05
CASE_DOMINANT = 0.8
FORMULA = (
    "table score = 100 * (1 - min(1, (blocking rows + 0.25 * warning rows) / rows)); "
    "source score = row weighted mean of the table scores. Blocking: required_null, invalid_date, out_of_range, "
    "invalid_number, orphan_fk, duplicate. Warning: whitespace, case_anomaly."
)


@dataclass
class ColumnRules:
    name: str
    semantic: str | None = None
    pii: bool = False
    required: bool = False
    date_formats: list[str] = field(default_factory=list)  # text dates
    epoch: date | None = None  # integer dates
    unit: str | None = None
    null_markers: list[int] = field(default_factory=list)
    invalid_markers: list[int] = field(default_factory=list)
    bounds: tuple[float, float] | None = None
    parent: str | None = None
    parent_keys: set[int] | None = None
    whitespace: bool = False
    case_minority: set[str] = field(default_factory=set)
    numeric_text: bool = False


@dataclass
class TableRules:
    name: str
    columns: list[ColumnRules]
    dedupe: list[str]


# ------------------------------------------------------------------ rule derivation


def derive_bounds(p: ColumnProfile) -> tuple[float, float] | None:
    n = p.numeric
    if n is None or n.p05 is None or n.p95 is None or p.semantic_type in NO_RANGE_SEMANTICS or p.pii:
        return None
    spread = n.p95 - n.p05
    pad = max(spread, abs(n.p95) * 0.5, 1.0) * RANGE_PAD
    low = 0.0 if n.negative_share <= NON_NEGATIVE_SHARE else n.p05 - pad
    return low, n.p95 + pad


def derive_rules(result: ProfileResult, key_sets: dict[str, set[int]]) -> list[TableRules]:
    rules: list[TableRules] = []
    for table in result.tables:
        columns = []
        for p in table.columns:
            r = ColumnRules(p.name, p.semantic_type, p.pii)
            nonempty = p.rows - p.null_count - p.blank_count
            r.required = p.rows >= MIN_ROWS_FOR_RULES and nonempty > 0 and p.null_rate <= MAX_NULL_RATE and p.semantic_type not in NO_REQUIRED_SEMANTICS
            r.null_markers, r.invalid_markers = p.null_markers, p.invalid_markers
            if p.inferred_type in ("date", "timestamp"):
                if p.date and p.date.epoch:
                    r.epoch, r.unit = date.fromisoformat(p.date.epoch), p.date.unit
                elif p.date:
                    r.date_formats = p.date.dominant
            elif p.inferred_type in ("integer", "decimal"):
                r.bounds = derive_bounds(p)
                r.numeric_text = bool(p.numeric and p.numeric.formats)
            if p.references and p.references in key_sets:
                r.parent, r.parent_keys = p.references, key_sets[p.references]
            text_like = {"text", "person_name", "address", "organization_name", "enumerated_code", "free_text"}
            if p.inferred_type == "text" and p.semantic_type in text_like and p.whitespace_share <= 0.1:
                r.whitespace = True
            if p.case and p.case.letters >= 30 and p.semantic_type in text_like:
                shares = {"upper": p.case.upper, "lower": p.case.lower, "title": p.case.title, "other": p.case.other}
                if max(shares.values()) >= CASE_DOMINANT:
                    r.case_minority = {k for k, v in shares.items() if v < CASE_MINORITY}
            columns.append(r)
        sequences = set(table.sequence_columns)
        dedupe = [c.name for c in table.columns if c.name not in sequences]
        # A table that is only a row number and one value column cannot have meaningful duplicate rows.
        rules.append(TableRules(table.name, columns, dedupe if len(dedupe) >= 2 else []))
    return rules


# ------------------------------------------------------------------ vectorised checks


def missing_mask(s: pd.Series, r: ColumnRules) -> pd.Series:
    mask = s.isna()
    if s.dtype == object or str(s.dtype).startswith("str"):
        mask = mask | s.map(lambda v: isinstance(v, str) and v.strip() == "")
    if r.null_markers:
        mask = mask | pd.to_numeric(s, errors="coerce").isin(r.null_markers)
    return mask


def _date_ok(s: pd.Series, r: ColumnRules) -> pd.Series:
    ok = missing_mask(s, r)
    if r.epoch is not None:
        values = pd.to_numeric(s, errors="coerce")
        scale = 1 if r.unit == "days" else 1440
        low, high = (date(YEAR_MIN, 1, 1) - r.epoch).days * scale, (date(YEAR_MAX, 12, 31) - r.epoch).days * scale
        return ok | ((values > 0) & (values >= low) & (values <= high) & ~values.isin(r.invalid_markers))
    text = s.astype(str).str.strip()
    for fmt in r.date_formats:
        rx = next(rx for f, rx, _ in DATE_FORMATS if f == fmt)
        shaped = text.str.match(rx)
        if not shaped.any():
            continue
        parsed = pd.to_datetime(text[shaped], format=fmt, errors="coerce")
        good = parsed.notna() & parsed.dt.year.between(YEAR_MIN, YEAR_MAX)
        ok.loc[good[good].index] = True
    return ok


def _numbers(s: pd.Series, r: ColumnRules) -> pd.Series:
    if not r.numeric_text:
        return pd.to_numeric(s, errors="coerce")
    txt = s.astype(str).str.strip().where(~s.isna(), "")
    masks = {name: txt.str.match(rx) for name, rx in NUMERIC_FORMATS.items()}
    return parse_numeric(txt, masks)


def _range_ok(s: pd.Series, r: ColumnRules) -> pd.Series:
    assert r.bounds is not None
    numbers = _numbers(s, r)
    return numbers.isna() | ((numbers >= r.bounds[0]) & (numbers <= r.bounds[1]))


def _number_ok(s: pd.Series, r: ColumnRules) -> pd.Series:
    return missing_mask(s, r) | _numbers(s, r).notna()


def _orphan_ok(s: pd.Series, r: ColumnRules) -> pd.Series:
    assert r.parent_keys is not None
    numbers = pd.to_numeric(s, errors="coerce")
    return missing_mask(s, r) | numbers.isin(r.parent_keys)


def _whitespace_ok(s: pd.Series) -> pd.Series:
    text = s.astype(str)
    return s.isna() | (text == text.str.strip())


def style_of(s: pd.Series) -> pd.Series:
    text = s.astype(str)
    lettered = text.str.contains(r"[A-Za-z]")
    upper, lower = text.str.isupper(), text.str.islower()
    first_upper = text.str.extract(r"([A-Za-z])")[0].str.isupper().fillna(False)
    title = ~upper & ~lower & first_upper & ~text.str.contains(r"[a-z][A-Z]")
    style = pd.Series("other", index=s.index)
    style[upper] = "upper"
    style[lower] = "lower"
    style[title] = "title"
    style[~lettered | s.isna()] = "none"
    return style


def _case_ok(s: pd.Series, r: ColumnRules) -> pd.Series:
    return ~style_of(s).isin(r.case_minority)


def build_schema(rules: TableRules) -> pa.DataFrameSchema:
    columns: dict[str, pa.Column] = {}
    for r in rules.columns:
        checks: list[pa.Check] = []
        if r.required:
            checks.append(pa.Check(lambda s, r=r: ~missing_mask(s, r), name="required_null", ignore_na=False))
        if r.epoch is not None or r.date_formats:
            checks.append(pa.Check(lambda s, r=r: _date_ok(s, r), name="invalid_date", ignore_na=False))
        if r.bounds is not None:
            checks.append(pa.Check(lambda s, r=r: _range_ok(s, r), name="out_of_range", ignore_na=False))
            if r.numeric_text:
                checks.append(pa.Check(lambda s, r=r: _number_ok(s, r), name="invalid_number", ignore_na=False))
        if r.parent_keys is not None:
            checks.append(pa.Check(lambda s, r=r: _orphan_ok(s, r), name="orphan_fk", ignore_na=False))
        if r.whitespace:
            checks.append(pa.Check(lambda s: _whitespace_ok(s), name="whitespace", ignore_na=False))
        if r.case_minority:
            checks.append(pa.Check(lambda s, r=r: _case_ok(s, r), name="case_anomaly", ignore_na=False))
        if checks:
            columns[r.name] = pa.Column(checks=checks, nullable=True, required=True)
    return pa.DataFrameSchema(columns, coerce=False, strict=False)


# ------------------------------------------------------------------ the run


def run_quality(tables: list[TableSource], rules: list[TableRules], *, chunk_size: int, max_issues: int = 200_000) -> QualityReport:
    rules_by_name = {r.name: r for r in rules}
    pii_columns = {(r.name, c.name) for r in rules for c in r.columns if c.pii}
    issues: list[QualityIssue] = []
    totals: Counter[str] = Counter()
    table_results: list[TableQuality] = []
    truncated = False

    def record(table: str, row: Any, kind: str, column: str | None, value: Any) -> None:
        nonlocal truncated
        totals[kind] += 1
        if len(issues) >= max_issues:
            truncated = True
            return
        shown = None if (table, column) in pii_columns or value is None or (isinstance(value, float) and pd.isna(value)) else str(value)[:60]
        issues.append(QualityIssue(table=table, row=int(row), type=kind, column=column, value=shown, blocking=kind in BLOCKING))

    for source in tables:
        table_rules = rules_by_name.get(source.name)
        if table_rules is None:
            continue
        schema = build_schema(table_rules)
        seen: set[int] = set()
        blocking_rows: set[int] = set()
        warning_rows: set[int] = set()
        per_type: Counter[str] = Counter()
        rows = 0
        for chunk in source.chunks(chunk_size):
            rows += len(chunk)
            try:
                schema.validate(chunk, lazy=True)
            except pa.errors.SchemaErrors as error:
                cases = error.failure_cases
                cases = cases[cases["index"].notna() & cases["column"].notna()]
                for case in cases.itertuples(index=False):
                    kind = str(case.check)
                    record(source.name, case.index, kind, str(case.column), case.failure_case)
                    per_type[kind] += 1
                    (blocking_rows if kind in BLOCKING else warning_rows).add(int(case.index))
            if table_rules.dedupe:
                hashes = pd.util.hash_pandas_object(chunk[table_rules.dedupe].astype(str), index=False)
                within = hashes.duplicated(keep="first")
                earlier = hashes.isin(seen)
                for row in chunk.index[(within | earlier).to_numpy()]:
                    record(source.name, row, "duplicate", None, None)
                    per_type["duplicate"] += 1
                    blocking_rows.add(int(row))
                seen.update(int(h) for h in hashes)
        warning_only = warning_rows - blocking_rows
        penalty = (len(blocking_rows) + WARNING_WEIGHT * len(warning_only)) / rows if rows else 0.0
        table_results.append(
            TableQuality(table=source.name, rows=rows, issues_by_type=dict(per_type), blocking_rows=len(blocking_rows),
                         warning_rows=len(warning_only), score=round(100 * (1 - min(1.0, penalty)), 2))  # fmt: skip
        )

    total_rows = sum(t.rows for t in table_results)
    score = round(sum(t.score * t.rows for t in table_results) / total_rows, 2) if total_rows else 100.0
    config = {
        r.name: {
            "required": [c.name for c in r.columns if c.required],
            "dates": [c.name for c in r.columns if c.epoch is not None or c.date_formats],
            "ranges": {c.name: [round(c.bounds[0], 4), round(c.bounds[1], 4)] for c in r.columns if c.bounds},
            "references": {c.name: c.parent for c in r.columns if c.parent},
            "duplicate_columns": r.dedupe,
        }
        for r in rules
    }
    return QualityReport(score=score, formula=FORMULA, tables=table_results, issue_counts=dict(totals), issues=issues,
                         issues_truncated=truncated, config=config)  # fmt: skip


__all__ = ["derive_rules", "run_quality", "build_schema", "FORMULA", "BLOCKING", "timedelta"]
