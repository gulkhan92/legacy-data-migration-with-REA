"""Rule based PII detection. It sets `pii` on a column and controls what may ever leave the system.

A column is PII when its values are identifying (by what the values look like) or its name says so. PII columns keep
no example values, no minimum or maximum and no top values in the stored profile, and an LLM sees only their pattern
class (see llm_view.py). Rules only: nothing here calls a model.
"""

from app.profiling.models import ColumnProfile

VALUE_KINDS = {
    "email": "email", "ssn": "national_id", "phone": "phone", "postal_code": "postal_code", "ip_address": "ip_address",
    "payment_card": "payment_card",
}  # fmt: skip
NAME_QUALIFIERS = {"first", "last", "given", "surname", "forename", "maiden", "middle", "full"}


def detect_pii(p: ColumnProfile) -> list[str]:
    words = set(p.expanded_name.split())
    kinds: list[str] = []
    if p.semantic_type in VALUE_KINDS:
        kinds.append(VALUE_KINDS[p.semantic_type])
    if p.semantic_type == "person_name" or ("name" in words and words & NAME_QUALIFIERS) or "patient" in words and "name" in words:
        kinds.append("person_name")
    if p.inferred_type == "text" and (p.semantic_type == "address" or ("street" in words and p.semantic_type not in ("email", "phone"))):
        kinds.append("street_address")
    if words & {"phone", "fax"}:
        kinds.append("phone")
    if words & {"email"} and p.inferred_type == "text":
        kinds.append("email")
    if words & {"ssn", "social", "passport", "license"} or ("national" in words and "id" in words) or ("tax" in words and "id" in words):
        kinds.append("national_id")
    if "birth" in words and p.inferred_type in ("date", "timestamp"):
        kinds.append("date_of_birth")
    if "death" in words and p.inferred_type in ("date", "timestamp"):
        kinds.append("death_date")
    if words & {"zip", "postal"} and p.inferred_type in ("text", "integer"):
        kinds.append("postal_code")
    if words & {"latitude", "longitude", "lat", "lon"}:
        kinds.append("geolocation")
    return sorted(set(kinds))


def redact(p: ColumnProfile) -> None:
    """Remove every value derived field from the profile of a PII column."""
    p.top_values = []
    p.code_values = []
    p.min = None
    p.max = None
    if p.date:
        p.date.min = None
        p.date.max = None
    if p.numeric:
        p.numeric.min = p.numeric.max = p.numeric.mean = None
        p.numeric.p01 = p.numeric.p05 = p.numeric.p95 = p.numeric.p99 = p.numeric.min_positive = None
    if p.length is not None:
        p.length = p.length  # lengths describe shape, not content


def apply_pii(p: ColumnProfile) -> None:
    kinds = detect_pii(p)
    p.pii_kinds = kinds
    p.pii = bool(kinds)
    if p.pii:
        redact(p)
