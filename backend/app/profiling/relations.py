"""Key and foreign key inference by value containment.

A column is a key candidate when its values are (nearly) unique integers. A column points at a key when almost all of
its values are found among the key's values. When several keys qualify, the one the column covers most wins: a real
foreign key uses much of the parent, while an unrelated small number column fits inside many large key ranges by luck.
"""

from collections import Counter
from dataclasses import dataclass

from app.profiling.models import ColumnProfile, ForeignKey, TableProfile

CONTAINMENT = 0.9
MIN_DISTINCT = 12  # columns with fewer distinct values are codes or flags, not references
KEY_UNIQUE = 0.95
MIN_KEY_SIZE = 5


@dataclass
class KeyValues:
    """Numeric values of a column with their row counts, for containment tests."""

    table: str
    column: str
    values: dict[int, int]  # value -> rows

    @property
    def name(self) -> str:
        return f"{self.table}.{self.column}"


def integer_values(counter: Counter[object], markers: list[int]) -> dict[int, int] | None:
    out: dict[int, int] = {}
    for value, count in counter.items():
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if number != int(number):
            return None
        if int(number) in markers:
            continue
        out[int(number)] = out.get(int(number), 0) + count
    return out


def infer_foreign_keys(tables: list[TableProfile], counters: dict[tuple[str, str], Counter[object]]) -> list[ForeignKey]:
    keys: list[KeyValues] = []
    children: list[KeyValues] = []
    by_name: dict[str, ColumnProfile] = {}
    for table in tables:
        for p in table.columns:
            if p.inferred_type != "integer":
                continue
            values = integer_values(counters[(table.name, p.name)], p.null_markers)
            if not values:
                continue
            by_name[f"{table.name}.{p.name}"] = p
            non_empty = max(1, p.rows - p.null_count - p.blank_count)
            if p.unique_ratio >= KEY_UNIQUE and len(values) >= MIN_KEY_SIZE:
                keys.append(KeyValues(table.name, p.name, values))
                p.key_candidate = True
            else:
                children.append(KeyValues(table.name, p.name, values))
            _ = non_empty

    found: list[ForeignKey] = []
    for child in children:
        profile = by_name[child.name]
        if len(child.values) < (MIN_DISTINCT if profile.layout_semantic != "ref" else 3):
            continue
        rows = sum(child.values.values())
        best: tuple[float, ForeignKey] | None = None
        for key in keys:
            if key.table == child.table:
                continue
            inside = sum(count for value, count in child.values.items() if value in key.values)
            containment = inside / rows if rows else 0.0
            if containment < CONTAINMENT:
                continue
            coverage = len(set(child.values) & set(key.values)) / len(key.values)
            candidate = ForeignKey(child=child.name, parent=key.name, containment=round(containment, 4), coverage=round(coverage, 4))
            if best is None or coverage > best[0]:
                best = (coverage, candidate)
        if best:
            found.append(best[1])
            profile.references = best[1].parent
            if profile.semantic_type in (None, "integer", "enumerated_code", "reference"):
                profile.semantic_type = "reference"
    return found
