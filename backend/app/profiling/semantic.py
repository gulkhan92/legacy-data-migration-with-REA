"""Deterministic semantic typing: regex and rules over value signals, uniqueness, type and the words of the column name.

Semantic types: email, phone, ssn, postal_code, date, timestamp, date_of_birth, currency, identifier, reference,
enumerated_code, boolean, person_name, address, organization_name, free_text, flags, integer, decimal, text, url,
ip_address, payment_card.

No model and no network are involved. Every decision is a rule on facts measured in the profile.
"""

import re
from datetime import date, datetime, timedelta

from app.profiling.models import ColumnProfile, DateStats
from app.profiling.vocabulary import expanded_words

SHARE = 0.8  # share of values that must carry a signal
MONEY_WORDS = {"price", "amount", "cost", "total", "fee", "expenses", "income", "balance", "cents"}
CODE_WORDS = {"code", "status", "class", "type", "flag", "category", "gender", "race", "ethnicity", "marital"}
ID_WORDS = {"id", "number", "key", "sequence"}
NAME_QUALIFIERS = {"first", "last", "given", "surname", "forename", "maiden", "middle", "full"}
EPOCHS = [("1970-01-01", date(1970, 1, 1)), ("1900-01-01", date(1900, 1, 1)), ("1899-12-30", date(1899, 12, 30))]
MAX_CODES = 50
CODE_SHARE = 0.05


def _is_text(p: ColumnProfile) -> bool:
    return p.inferred_type == "text"


def apply_layout_hints(p: ColumnProfile) -> None:
    """Fields of binary files arrive with a semantic hint from discovery (declared by the application or guessed)."""
    hint = (p.layout_semantic or "").rstrip("?")
    if hint in ("epoch_days", "epoch_minutes") and p.numeric:
        unit = "days" if hint == "epoch_days" else "minutes"
        p.date = infer_epoch(p, unit)
        p.inferred_type = "date" if unit == "days" else "timestamp"
        p.semantic_type = "timestamp" if unit == "minutes" else "date"
        if p.numeric.min_positive is not None and p.date.epoch:
            origin = date.fromisoformat(p.date.epoch)
            span = (lambda n: origin + timedelta(days=n)) if unit == "days" else (lambda n: datetime.combine(origin, datetime.min.time()) + timedelta(minutes=n))
            p.min = span(int(p.numeric.min_positive)).isoformat()[:19]
            p.max = span(int(p.numeric.max or 0)).isoformat()[:19]
            p.date.min, p.date.max = p.min, p.max
        if p.numeric:
            zeros = p.numeric.p01 is not None
            p.null_markers = [0] if zeros else []
            p.invalid_markers = [-1]
    elif hint == "key":
        p.semantic_type = "identifier"
    elif hint == "cents":
        p.semantic_type = "currency"
    elif hint == "code":
        p.semantic_type = "enumerated_code"
    elif hint == "bit_flags":
        p.semantic_type = "flags"
    elif hint == "ref":
        p.semantic_type = "reference"
        p.null_markers = [0]


def infer_epoch(p: ColumnProfile, unit: str) -> DateStats:
    """Pick the epoch under which the largest stored value is not in the future (a recorded event cannot be)."""
    stats = DateStats(unit=unit)
    assert p.numeric is not None
    top = p.numeric.max or 0
    low = p.numeric.min_positive
    today = date.today() + timedelta(days=366)
    for label, origin in EPOCHS:
        days = top if unit == "days" else top / 1440
        end = origin + timedelta(days=int(days))
        start_ok = low is None or origin + timedelta(days=int(low if unit == "days" else low / 1440)) >= date(1800, 1, 1)
        if end <= today and start_ok:
            stats.epoch = label
            break
    stats.has_time = unit == "minutes"
    return stats


def classify(p: ColumnProfile) -> None:
    """Set semantic_type for columns that are not already typed by their layout hint."""
    words = set(p.expanded_name.split())
    non_empty = p.rows - p.null_count - p.blank_count
    signals = p.signals

    if p.semantic_type in ("date", "timestamp", "flags", "reference", "currency", "enumerated_code") and p.layout_semantic:
        if p.semantic_type == "reference" and not p.numeric:
            return
        return refine_date(p, words)

    if p.inferred_type in ("date", "timestamp"):
        p.semantic_type = p.inferred_type
        return refine_date(p, words)
    if p.inferred_type == "boolean":
        p.semantic_type = "boolean"
        return
    if _is_text(p):
        for name, label in (("email", "email"), ("ssn", "ssn"), ("url", "url"), ("ipv4", "ip_address"), ("payment_card", "payment_card")):
            if signals.get(name, 0) >= 0.9:
                p.semantic_type = label
                return
        if signals.get("uuid", 0) >= 0.9:
            p.semantic_type = "identifier"
            return
        if signals.get("phone", 0) >= SHARE or (words & {"phone", "fax"} and signals.get("phone", 0) >= 0.5):
            p.semantic_type = "phone"
            return
        if (words & {"zip", "postal"} and signals.get("postal", 0) >= 0.7) or signals.get("postal", 0) >= 0.9 and p.pattern_class and "-" in p.pattern_class:
            p.semantic_type = "postal_code"
            return
        if words & {"address", "street"}:
            p.semantic_type = "address"
            return
        if "name" in words and words & NAME_QUALIFIERS:
            p.semantic_type = "person_name"
            return
        if "name" in words and "company" in words:
            p.semantic_type = "organization_name"
            return
    numeric = p.inferred_type in ("integer", "decimal")
    if numeric and (p.numeric and p.numeric.currency_symbols or words & MONEY_WORDS):
        p.semantic_type = "currency"
        return
    unique = p.unique_ratio >= 0.95 and non_empty >= 20
    if unique and (numeric or _is_text(p)) and (words & ID_WORDS or (numeric and sequential(p))):
        p.semantic_type = "identifier"
        p.key_candidate = True
        return
    few = p.distinct_count <= MAX_CODES and (p.distinct_count <= CODE_SHARE * max(non_empty, 1) or words & CODE_WORDS)
    if few and non_empty >= 20 and (numeric or _is_text(p)):
        p.semantic_type = "enumerated_code"
        return
    if _is_text(p) and p.length and p.length.mean >= 40:
        p.semantic_type = "free_text"
        return
    p.semantic_type = p.inferred_type if p.inferred_type in ("integer", "decimal") else "text"


def refine_date(p: ColumnProfile, words: set[str]) -> None:
    if words & {"birth"}:
        p.semantic_type = "date_of_birth"
    elif p.semantic_type == "timestamp" and not p.date:
        p.semantic_type = "timestamp"


def sequential(p: ColumnProfile) -> bool:
    """Counts up from one without gaps: a row number or a generated key."""
    if not p.numeric or p.numeric.min is None or p.numeric.max is None or not p.numeric.integer_valued:
        return False
    return p.numeric.min == 1 and abs(p.numeric.max - p.distinct_count) <= max(1, 0.02 * p.distinct_count) and p.unique_ratio >= 0.95


def words_of(name: str) -> set[str]:
    return set(expanded_words(name))


def has_word(name: str, *candidates: str) -> bool:
    return bool(words_of(name) & set(candidates))


__all__ = ["apply_layout_hints", "classify", "infer_epoch", "sequential", "words_of", "has_word", "re"]
