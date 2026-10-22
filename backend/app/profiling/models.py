"""Profiling and quality result models."""

from typing import Any

from pydantic import BaseModel, Field


class PatternCount(BaseModel):
    pattern: str
    count: int
    share: float


class ValueCount(BaseModel):
    value: str
    count: int


class LengthStats(BaseModel):
    min: int
    max: int
    mean: float
    p95: int


class NumericStats(BaseModel):
    parsed: int  # values that read as numbers
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    p01: float | None = None
    p05: float | None = None
    p95: float | None = None
    p99: float | None = None
    min_positive: float | None = None
    negative_share: float = 0.0
    formats: dict[str, int] = Field(default_factory=dict)  # plain, currency, thousands, decimal_comma, percent, accounting
    currency_symbols: list[str] = Field(default_factory=list)
    integer_valued: bool = False


class DateFormatStat(BaseModel):
    shape: int  # values that look like this format
    valid: int  # values that really are a date in this format


class DateStats(BaseModel):
    formats: dict[str, DateFormatStat] = Field(default_factory=dict)
    dominant: list[str] = Field(default_factory=list)  # formats accepted when checking dates
    ambiguous: bool = False  # day first and month first read equally well
    epoch: str | None = None  # for integer date columns: the day the count starts from
    unit: str | None = None  # days or minutes, for integer date columns
    min: str | None = None
    max: str | None = None
    has_time: bool = False


class CaseStats(BaseModel):
    upper: float = 0.0
    lower: float = 0.0
    title: float = 0.0
    other: float = 0.0
    letters: int = 0


class ColumnProfile(BaseModel):
    table: str
    name: str
    position: int
    rows: int
    null_count: int
    blank_count: int
    null_rate: float  # null, blank text and the null marker of a legacy field, divided by rows
    distinct_count: int
    distinct_approximate: bool = False
    unique_ratio: float = 0.0  # distinct values among non-empty values
    inferred_type: str  # integer, decimal, boolean, date, timestamp, text
    semantic_type: str | None = None
    pattern_class: str | None = None
    pii: bool = False
    pii_kinds: list[str] = Field(default_factory=list)
    declared_type: str | None = None
    layout_semantic: str | None = None
    expanded_name: str = ""
    min: str | None = None
    max: str | None = None
    length: LengthStats | None = None
    numeric: NumericStats | None = None
    date: DateStats | None = None
    case: CaseStats | None = None
    top_patterns: list[PatternCount] = Field(default_factory=list)
    top_values: list[ValueCount] = Field(default_factory=list)  # never filled for PII columns
    code_values: list[ValueCount] = Field(default_factory=list)  # every value of a column with at most 50 distinct values, never for PII
    whitespace_share: float = 0.0
    signals: dict[str, float] = Field(default_factory=dict)  # share of values that look like an email, phone, postal code, ...
    key_candidate: bool = False
    references: str | None = None  # "TABLE.column" of the key this column points at
    null_markers: list[int] = Field(default_factory=list)
    invalid_markers: list[int] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ForeignKey(BaseModel):
    child: str  # "TABLE.column"
    parent: str
    containment: float  # share of child values found among the parent keys
    coverage: float  # share of the parent keys used by the child


class TableProfile(BaseModel):
    name: str
    rows: int
    columns: list[ColumnProfile]
    sequence_columns: list[str] = Field(default_factory=list)  # row numbers, ignored by duplicate detection


class ProfileResult(BaseModel):
    source_kind: str
    tables: list[TableProfile]
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    rows_total: int = 0
    chunk_size: int = 0
    limitations: list[str] = Field(default_factory=list)


class QualityIssue(BaseModel):
    table: str
    row: int  # rowid for SQLite sources, zero based record or line index otherwise
    type: str  # required_null, invalid_date, out_of_range, orphan_fk, duplicate, whitespace, case_anomaly
    column: str | None = None
    value: str | None = None  # absent for PII columns
    blocking: bool = True


class TableQuality(BaseModel):
    table: str
    rows: int
    issues_by_type: dict[str, int] = Field(default_factory=dict)
    blocking_rows: int = 0
    warning_rows: int = 0
    score: float = 100.0


class QualityReport(BaseModel):
    score: float
    formula: str
    tables: list[TableQuality]
    issue_counts: dict[str, int]
    issues: list[QualityIssue]
    issues_truncated: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
