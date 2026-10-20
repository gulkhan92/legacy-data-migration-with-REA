"""Score discovery results against the ground truth of the sample legacy systems.

Used by the tests and by the evaluation harness (Phase 13). The platform itself never reads a ground truth.
A true field counts as recovered when name, type and size (length, for fixed layouts) all agree.
"""

from dataclasses import dataclass, field
from typing import Any

from app.discovery.models import LayoutSpec, SourceSchema

TYPE_FAMILY = {
    "char": "text", "int8": "int", "uint8": "int", "int16": "int", "uint16": "int", "int32": "int", "uint32": "int",
    "int64": "int", "uint64": "int", "bytes": "bytes",
}  # fmt: skip


@dataclass
class Score:
    total: int = 0
    recovered: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.recovered / self.total if self.total else 1.0

    def add(self, ok: bool, label: str) -> None:
        self.total += 1
        if ok:
            self.recovered += 1
        else:
            self.missing.append(label)


def score_sqlite(schema: SourceSchema, truth: dict[str, Any]) -> Score:
    """Tables, columns (name and declared type) and the other database objects."""
    score = Score()
    found = {t.name: {c.name: (c.declared_type or "").upper() for c in t.columns} for t in schema.tables}
    for table, spec in truth["legacy_tables"].items():
        score.add(table in found, f"table {table}")
        for column in spec["columns"]:
            ok = found.get(table, {}).get(column["name"]) == column["legacy_type"].upper()
            score.add(ok, f"{table}.{column['name']}")
    for table in truth.get("system_tables", {}):
        score.add(table in found, f"table {table}")
    # indexes, view and trigger declared by the fixture builder
    score.add({i.name for i in schema.indexes} >= {"IX_INV_CUST", "IX_DTL_INV", "IX_CUST_NM"}, "indexes")
    score.add("V_CUST_LIST" in {v.name for v in schema.views}, "view V_CUST_LIST")
    score.add("TRG_INV_LOG" in {t.name for t in schema.triggers}, "trigger TRG_INV_LOG")
    return score


def score_layout(layout: LayoutSpec, truth_layout: dict[str, Any], *, by: str = "name") -> Score:
    """Fields of every table. by="name" needs name, type and length. by="position" needs offset, length and
    type family, for results whose field names are generated."""
    score = Score()
    for table in truth_layout["tables"]:
        found = next(
            (t for t in layout.tables if t.name == table["name"] or (by == "position" and t.record_size == table["record_size"]
                                                                       and t.data_offset == table["data_offset"])),
            None,
        )  # fmt: skip
        score.add(found is not None and found.record_size == table["record_size"], f"table {table['name']}")
        for f in table["fields"]:
            ok = False
            if found is not None:
                for g in found.fields:
                    if by == "name":
                        ok = g.name == f["name"] and g.type == f["type"] and g.length == f["length"]
                    else:
                        ok = g.offset == f["offset"] and g.length == f["length"] and TYPE_FAMILY.get(g.type) == TYPE_FAMILY.get(f["type"])
                    if ok:
                        break
            score.add(ok, f"{table['name']}.{f['name']}")
    return score
