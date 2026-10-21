"""What an LLM is allowed to see about a column.

The token minimisation rules of the plan: metadata only, never full rows, and never values from PII columns (only the
pattern class). A column is described by its normalised name, inferred type, pattern class and at most five distinct
masked sample values. The stored profile of a PII column holds no values at all, so this function cannot leak them even
by mistake.
"""

import re
from typing import Any

from app.profiling.models import ColumnProfile

MAX_SAMPLES = 5
MAX_SAMPLE_LENGTH = 40


def mask_value(value: str) -> str:
    """Keep the look of the value, hide long digit runs (identifiers, account numbers)."""
    text = re.sub(r"\d{6,}", lambda m: "9" * len(m.group()), value)
    return text[:MAX_SAMPLE_LENGTH]


def llm_column_view(p: ColumnProfile) -> dict[str, Any]:
    view: dict[str, Any] = {
        "name": p.expanded_name or p.name.lower(),
        "type": p.inferred_type,
        "semantic_type": p.semantic_type,
        "pattern_class": p.pattern_class,
        "pii": p.pii,
        "samples": [],
    }
    if p.pii:
        return view  # pattern class only
    seen: list[str] = []
    for item in p.top_values:
        masked = mask_value(item.value)
        if masked not in seen:
            seen.append(masked)
        if len(seen) == MAX_SAMPLES:
            break
    view["samples"] = seen
    return view
