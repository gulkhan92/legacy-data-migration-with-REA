"""The text that is embedded for a source column and for a target field.

A source column is described by its table, its name with abbreviations expanded, its semantic type, its pattern class and
a few masked examples (never values of a PII column: the same metadata view that an LLM may see is used). A target field is
described by its table, name, type, description and synonyms. Both sides use plain language so that a small general model
can match `l_nm` in `CUST_MST` with `customers.last_name`.
"""

from typing import Any

from app.profiling.llm_view import llm_column_view
from app.profiling.models import ColumnProfile
from app.profiling.vocabulary import expanded_name

# bge models are trained to embed a short query with this instruction when it is matched against longer passages
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_EXAMPLES = 3
MAX_EXAMPLE_LENGTH = 20
TYPE_WORDS = {
    "email": "email address", "phone": "telephone number", "ssn": "social security number", "postal_code": "postal code",
    "date": "date", "timestamp": "date and time", "date_of_birth": "date of birth", "currency": "money amount",
    "identifier": "identifier key", "reference": "reference to another record", "enumerated_code": "coded value",
    "boolean": "yes or no flag", "person_name": "person name", "address": "street address", "organization_name": "company name",
    "free_text": "free text", "flags": "packed flags", "integer": "whole number", "decimal": "decimal number", "text": "text",
    "url": "web address", "ip_address": "ip address", "payment_card": "card number",
}  # fmt: skip


def source_text(table: str, p: ColumnProfile, *, note: str | None = None) -> str:
    """The description embedded for a source column. `note` is documentation found next to a binary field."""
    view = llm_column_view(p)
    parts = [
        f"table {expanded_name(table)}",
        f"column {p.expanded_name or p.name.lower()}",
        f"type {TYPE_WORDS.get(p.semantic_type or '', p.inferred_type)}",
    ]
    if p.references:
        parts.append(f"refers to {expanded_name(p.references.split('.')[0])}")
    if note:
        parts.append(note[:120])
    examples = [s[:MAX_EXAMPLE_LENGTH] for s in view["samples"][:MAX_EXAMPLES]]
    if examples:
        parts.append("examples " + ", ".join(examples))
    elif p.pattern_class and not p.pii and p.inferred_type == "text":
        parts.append(f"pattern {p.pattern_class}")
    return " | ".join(parts)


def target_text(field: dict[str, Any]) -> str:
    """The description embedded for a target field (a row of a target schema file)."""
    words = field["name"].replace("_", " ")
    parts = [f"table {field['table'].replace('_', ' ')}", f"field {words}", f"type {field['type']}", field["description"]]
    if field.get("synonyms"):
        parts.append("also called " + ", ".join(field["synonyms"]))
    return " | ".join(parts)
