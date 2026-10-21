"""The type compatibility gate.

A date column is never mapped to a numeric field, whatever the similarity says. The gate looks at what the profile found
(inferred type and semantic type) and at the declared type of the target field.
"""

from app.profiling.models import ColumnProfile

# source family -> target types it may feed
TEXTUAL = {"identifier", "enumerated_code", "flags", "boolean"}


def source_family(p: ColumnProfile) -> str:
    semantic = p.semantic_type or ""
    if semantic == "identifier":
        return "identifier"
    if semantic == "reference":
        return "reference"
    if semantic == "flags":
        return "flags"
    if semantic == "enumerated_code":
        return "code"
    if p.inferred_type in ("date", "timestamp", "boolean"):
        return p.inferred_type
    if p.inferred_type in ("integer", "decimal"):
        return p.inferred_type
    return "text"


COMPATIBLE: dict[str, set[str]] = {
    "text": {"text"},
    "integer": {"integer", "decimal", "reference"},
    "decimal": {"decimal", "integer"},
    "date": {"date", "timestamp"},
    "timestamp": {"timestamp", "date"},
    "boolean": {"boolean", "text", "integer"},
    "identifier": {"text", "integer"},  # a key becomes the legacy identifier of the record, not a reference
    "reference": {"reference", "integer"},
    "flags": {"text", "boolean", "integer"},
    "code": {"text", "integer", "boolean"},
}


def compatible(p: ColumnProfile, target_type: str) -> bool:
    return target_type in COMPATIBLE[source_family(p)]
