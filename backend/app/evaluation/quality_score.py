"""Score quality findings against the defects injected into the sample systems (ground truth).

A defect counts as detected when the report holds an issue of the matching type on the same row. When both name a
column, the column match is reported separately. Precision is informational: the sample data has natural oddities
that are legitimately reported, and the injected defect list does not claim to be exhaustive.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.discovery.models import LayoutSpec
from app.profiling.models import QualityReport

MATCHING = {
    "duplicate": {"duplicate"}, "null_required": {"required_null"}, "invalid_date": {"invalid_date"},
    "out_of_range": {"out_of_range", "invalid_number"}, "orphan_fk": {"orphan_fk"}, "trailing_space": {"whitespace"},
    "mixed_case": {"case_anomaly"},
}  # fmt: skip
CORE_TYPES = ["duplicate", "null_required", "invalid_date", "out_of_range", "orphan_fk"]


@dataclass
class TypeScore:
    injected: int = 0
    detected: int = 0
    column_matched: int = 0
    flagged_rows: int = 0  # rows the report flagged with this kind of issue
    flagged_true: int = 0  # of those, rows that carry an injected defect of this kind
    missed: list[tuple[str, int]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.detected / self.injected if self.injected else 1.0

    @property
    def precision(self) -> float:
        return self.flagged_true / self.flagged_rows if self.flagged_rows else 1.0


@dataclass
class QualityScore:
    by_type: dict[str, TypeScore]

    @property
    def injected(self) -> int:
        return sum(s.injected for s in self.by_type.values())

    @property
    def detected(self) -> int:
        return sum(s.detected for s in self.by_type.values())

    @property
    def recall(self) -> float:
        return self.detected / self.injected if self.injected else 1.0

    @property
    def core_recall(self) -> float:
        core = [self.by_type[t] for t in CORE_TYPES if t in self.by_type]
        injected = sum(s.injected for s in core)
        return sum(s.detected for s in core) / injected if injected else 1.0


def aliases_from_layout(layout: LayoutSpec) -> dict[tuple[str, str], set[str]]:
    """Binary fields are named after the legacy code, the defects after the input column the code reads each field from.
    Discovery records that column as the field's source hint, which links the two."""
    return {(t.name, f.source_hint): {f.name} for t in layout.tables for f in t.fields if f.source_hint}


def score_quality(
    report: QualityReport,
    defects: list[dict[str, Any]],
    *,
    table_names: dict[str, str] | None = None,
    column_aliases: dict[tuple[str, str], set[str]] | None = None,
) -> QualityScore:
    """`table_names` maps the ground truth table name to the report's table name when they differ, and
    `column_aliases` maps (report table, ground truth column) to the report columns that stand for it."""
    names = table_names or {}
    aliases = column_aliases or {}
    found: dict[tuple[str, int, str], set[str | None]] = defaultdict(set)
    flagged: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for issue in report.issues:
        found[(issue.table, issue.row, issue.type)].add(issue.column)
        flagged[issue.type].add((issue.table, issue.row))

    scores: dict[str, TypeScore] = defaultdict(TypeScore)
    truth_rows: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for d in defects:
        table = names.get(d["table"], d["table"])
        row = d["rowid"] if "rowid" in d else d["row_index"]
        score = scores[d["type"]]
        score.injected += 1
        truth_rows[d["type"]].add((table, row))
        hit = None
        for issue_type in MATCHING[d["type"]]:
            if (table, row, issue_type) in found:
                hit = found[(table, row, issue_type)]
                break
        if hit is None:
            score.missed.append((table, row))
            continue
        score.detected += 1
        column = d.get("column")
        mapped = {c for c in hit if c is not None}
        accepted = aliases.get((table, str(column)), set())
        if column is None or not mapped or any(c.lower() == str(column).lower() or c in accepted for c in mapped):
            score.column_matched += 1
    for defect_type, score in scores.items():
        rows: set[tuple[str, int]] = set()
        for issue_type in MATCHING[defect_type]:
            rows |= flagged[issue_type]
        score.flagged_rows = len(rows)
        score.flagged_true = len(rows & truth_rows[defect_type])
    return QualityScore(dict(scores))
