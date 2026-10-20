"""Internal models produced by discovery: SourceSchema (databases) and LayoutSpec (binary files)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    declared_type: str | None = None
    nullable: bool = True
    primary_key_position: int = 0
    hidden: bool = False


class IndexInfo(BaseModel):
    name: str
    table: str
    sql: str | None = None


class TriggerInfo(BaseModel):
    name: str
    table: str
    sql: str | None = None


class ViewInfo(BaseModel):
    name: str
    sql: str | None = None


class TableInfo(BaseModel):
    name: str
    kind: str = "table"
    sql: str | None = None
    without_rowid: bool = False
    columns: list[ColumnInfo] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    sample_truncated: bool = False


class SourceSchema(BaseModel):
    """What was found in a legacy database."""

    kind: Literal["sqlite"] = "sqlite"
    engine: str | None = None
    database_encoding: str | None = None
    tables: list[TableInfo] = Field(default_factory=list)
    views: list[ViewInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    triggers: list[TriggerInfo] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class FieldLayout(BaseModel):
    name: str
    offset: int
    length: int
    type: str  # char, int8, uint8, int16, uint16, int32, uint32, int64, uint64, bytes
    encoding: str | None = None
    endianness: str | None = None
    semantic: str | None = None
    padding: str | None = None
    source_hint: str | None = None  # the name of the input column the legacy code reads this field from
    name_source: Literal["declared", "source_hint", "generated"] = "generated"
    confidence: float = 0.0
    method: Literal["cil", "statistical", "structure"] = "cil"
    note: str | None = None


class RecordLayout(BaseModel):
    name: str
    entity_hint: str | None = None
    table_id: int | None = None
    record_size: int
    record_count: int
    data_offset: int
    writer: str | None = None
    fields: list[FieldLayout] = Field(default_factory=list)
    confidence: float = 0.0


class HeaderLayout(BaseModel):
    size: int
    fields: list[FieldLayout] = Field(default_factory=list)
    directory_offset: int | None = None
    directory_entry_size: int | None = None
    directory_fields: list[FieldLayout] = Field(default_factory=list)


class FieldCheck(BaseModel):
    table: str
    field: str
    checked: int
    passed: int

    @property
    def ratio(self) -> float:
        return self.passed / self.checked if self.checked else 1.0


class ValidationReport(BaseModel):
    size_rule_ok: bool = False
    computed_size: int | None = None
    file_size: int | None = None
    tables_matched: bool = False
    records_checked: int = 0
    field_checks: list[FieldCheck] = Field(default_factory=list)
    passed: bool = False
    problems: list[str] = Field(default_factory=list)


class Ambiguity(BaseModel):
    kind: str
    method: str
    il_offset: int | None = None
    detail: str
    excerpt: str = ""  # compact evidence for the interpret_layout LLM task, capped at 1,500 tokens


class LayoutSpec(BaseModel):
    """The record layout of a proprietary binary data file."""

    format: str | None = None  # magic text when the file starts with one
    method: Literal["cil", "statistical"]
    confidence: float
    validated: bool
    file_size: int | None = None
    header: HeaderLayout | None = None
    tables: list[RecordLayout] = Field(default_factory=list)
    validation: ValidationReport | None = None
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    rejected_cil_layout: bool = False  # True when the CIL layout failed validation and was replaced
