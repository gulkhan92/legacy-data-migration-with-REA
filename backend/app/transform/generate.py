"""Deterministic generation of transform rules from the profile of a source column and the target field it is mapped to.

Nothing here calls a model. A rule is generated only from facts the profile measured (formats, epoch, markers, semantic type,
casing) and from documentation found next to a binary field. When the data cannot say what a conversion should be (what the
codes 1, 2, 3 of a status column mean, how a bit field splits into values) the rule is marked `needs_input` and lists what is
missing, so a person supplies it during review. An LLM hint on a mapping is used only when it names a whitelisted operation that
fits the types and needs no facts the profile lacks.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.profiling.models import ColumnProfile
from app.transform.dsl import OPS, RuleError, parse_steps

READY, NEEDS_INPUT = "ready", "needs_input"
TEXT_LIKE = {"text", "person_name", "address", "organization_name", "enumerated_code", "free_text", "postal_code", "phone", "email", "ssn"}
CASE_DOMINANT = 0.8
CASE_NATURAL_LIMIT = 0.10  # more than this share written all upper, all lower or inverted is not an anomaly but the data's style
BIT_NOTE = re.compile(r"bit\s+(\d+)\s+(male|female|deceased|hispanic|married)", re.I)
STRPTIME = {  # the formats the profile detects, as the DSL spells them
    "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d", "%d-%b-%y", "%d-%b-%Y", "%d.%m.%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
}  # fmt: skip


@dataclass
class GeneratedRule:
    steps: list[dict[str, Any]]
    status: str = READY
    notes: list[str] = field(default_factory=list)
    origin: str = "deterministic"
    missing: dict[str, Any] = field(default_factory=dict)  # what a person has to supply, for needs_input


def _case_step(p: ColumnProfile) -> dict[str, Any] | None:
    """Fix only the odd ones out: when almost every value is in title style, all upper, all lower and inverted values are anomalies."""
    c = p.case
    if c is None or c.letters < 30 or c.title < CASE_DOMINANT or p.semantic_type not in TEXT_LIKE - {"email", "ssn", "phone", "postal_code"}:
        return None
    if c.upper + c.lower > CASE_NATURAL_LIMIT:
        return None
    multiword = [pt for pt in p.top_patterns if " " in pt.pattern]
    title_words = sum(pt.share for pt in multiword if all(tok.startswith("A") for tok in pt.pattern.split(" ") if tok and tok[0] in "Aa"))
    sentence = sum(pt.share for pt in multiword) - title_words
    mode = "capitalize" if multiword and sentence > title_words else "title"
    return {"op": "case", "mode": mode, "styles": ["upper", "lower", "inverted"]}


def _date_formats(p: ColumnProfile) -> list[str]:
    if p.date is None:
        return []
    return [f for f in p.date.dominant if f in STRPTIME]


def _numeric_symbols(p: ColumnProfile) -> dict[str, Any]:
    formats = (p.numeric.formats if p.numeric else {})
    if "decimal_comma" in formats and formats["decimal_comma"] >= max(formats.get("plain", 0), formats.get("currency", 0)):
        return {"thousands": ".", "decimal": ","}
    return {"thousands": ",", "decimal": "."}


def flag_bits(note: str | None) -> dict[str, int]:
    """{"male": 0, "deceased": 1, ...} from a note such as "bit 0 male, bit 1 deceased"."""
    return {m.group(2).lower(): int(m.group(1)) for m in BIT_NOTE.finditer(note or "")}


def generate(p: ColumnProfile, target_type: str, target_name: str, *, note: str | None = None, hint: str | None = None) -> GeneratedRule:
    """The rule for one mapped column."""
    semantic = p.semantic_type or ""
    inferred = p.inferred_type
    markers = list(p.null_markers)
    no_rule = GeneratedRule([], NEEDS_INPUT, [f"No rule is known for a {semantic or inferred} column feeding a {target_type} field"])

    if target_type == "text":
        if semantic == "flags":
            bits = flag_bits(note)
            if target_name == "gender" and "male" in bits:
                return GeneratedRule([{"op": "bit_flag", "bit": bits["male"], "true_value": "M", "false_value": "F"}], READY, [f"Gender from bit {bits['male']} (male) of the flags, as documented in the code"])
            return GeneratedRule([], NEEDS_INPUT, ["Packed flags need a decision on how they become text"], missing={"flags": True, "note": note})
        if inferred in ("integer", "decimal") or semantic in ("identifier", "reference"):
            if semantic == "enumerated_code":
                codes = [c.value for c in p.code_values]
                return GeneratedRule([{"op": "map_codes", "mapping": {}}], NEEDS_INPUT, ["The meaning of numeric codes is not in the data: supply the code map"],
                                     missing={"codes": codes})  # fmt: skip
            return GeneratedRule([{"op": "to_text", "null_values": markers}] if markers else [{"op": "to_text"}])
        steps: list[dict[str, Any]] = [{"op": "trim"}]
        if semantic == "phone":
            steps.append({"op": "normalize_phone"})
        if semantic == "email":
            steps.append({"op": "case", "mode": "lower"})
        elif (case := _case_step(p)) is not None:
            steps.append(case)
        return GeneratedRule(steps)

    if target_type == "integer":
        if inferred in ("integer", "decimal"):
            return GeneratedRule([{"op": "cast_int", "null_values": markers}] if markers else [{"op": "cast_int"}])
        return no_rule

    if target_type == "decimal":
        if inferred == "integer" and hint == "cents_to_decimal" or (inferred == "integer" and semantic == "currency" and (p.layout_semantic == "cents")):
            return GeneratedRule([{"op": "cents_to_decimal"}], origin="hint" if hint == "cents_to_decimal" else "deterministic")
        if inferred in ("decimal", "integer", "text") and (p.numeric is not None):
            return GeneratedRule([{"op": "cast_decimal", **_numeric_symbols(p), "scale": 2}])
        return no_rule

    if target_type in ("date", "timestamp"):
        if p.date and p.date.epoch and p.date.unit in ("days", "minutes"):
            op = "epoch_days_to_date" if p.date.unit == "days" else "epoch_minutes_to_timestamp"
            if (target_type == "date") != (p.date.unit == "days"):
                return GeneratedRule([], NEEDS_INPUT, [f"A {p.date.unit} count cannot feed a {target_type} field without a decision"])
            return GeneratedRule([{"op": op, "epoch": p.date.epoch, "null_values": markers, "invalid_values": list(p.invalid_markers)}])
        formats = _date_formats(p)
        if inferred in ("date", "timestamp") and formats:
            op = "parse_date" if target_type == "date" else "parse_timestamp"
            if target_type == "date" and any("%H" in f for f in formats):
                return GeneratedRule([], NEEDS_INPUT, ["A timestamp feeds a date field: say whether the time of day is dropped"])
            note_text = ["Day first and month first both read these dates; month first was chosen"] if p.date and p.date.ambiguous else []
            return GeneratedRule([{"op": op, "formats": formats}], READY, note_text)
        return GeneratedRule([], NEEDS_INPUT, ["The date formats of this column are not known"], missing={"formats": True})

    if target_type == "boolean":
        if inferred == "boolean":
            return GeneratedRule([{"op": "to_boolean"}])
        return GeneratedRule([], NEEDS_INPUT, ["No rule is known for turning this column into a yes or no value"])

    if target_type == "reference":
        if inferred in ("integer", "text", "decimal"):
            return GeneratedRule([{"op": "to_text", "null_values": markers}] if markers else [{"op": "to_text"}], READY, ["Holds the legacy key of the referenced record; resolved to the loaded record at load time"])
        return no_rule
    return no_rule


def check_rule(steps: list[dict[str, Any]], n_inputs: int = 1) -> None:
    """Raises RuleError when generated steps are not valid DSL (a bug in the generator, caught early)."""
    if steps:
        parse_steps(steps, n_inputs)


__all__ = ["GeneratedRule", "generate", "flag_bits", "check_rule", "READY", "NEEDS_INPUT", "OPS", "RuleError"]
