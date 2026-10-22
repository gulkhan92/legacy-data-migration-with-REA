"""Apply a table plan to a chunk of source rows: transformed values, and the reason a row cannot be loaded.

Row level reasons found here (values only, no other table needed). The database adds the rest at load time: orphan_fk,
duplicate and duplicate_key.

    required_null  a required target field has no value (the source was empty)
    invalid_date, invalid_number, invalid_code, invalid_value  a value could not be converted
    out_of_range   a number far outside the range of its column (the same limits the quality check uses)

A row with several problems gets the first reason in that order, and its detail names them all.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from app.transform.dsl import RuleOutput, apply_steps
from app.transform.plan import DERIVED_PREFIX, RuleSpec, TablePlan

REASON_ORDER = ["required_null", "invalid_date", "invalid_number", "invalid_code", "invalid_value", "too_long", "out_of_range"]


@dataclass
class RuleStats:
    """What one rule did to a chunk, summed across chunks by the caller."""

    rows: int = 0
    nulls: int = 0  # empty after the rule (the source was empty, or a null marker)
    failures: int = 0
    out_of_range: int = 0
    step_failures: list[int] = field(default_factory=list)

    def add(self, other: "RuleStats") -> None:
        self.rows += other.rows
        self.nulls += other.nulls
        self.failures += other.failures
        self.out_of_range += other.out_of_range
        if not self.step_failures:
            self.step_failures = list(other.step_failures)
        else:
            self.step_failures = [a + b for a, b in zip(self.step_failures, other.step_failures, strict=False)]


@dataclass
class Transformed:
    values: pd.DataFrame  # one object column per target field, index = source row identifier
    reasons: pd.Series  # first reason per row, None for a row that can be loaded
    details: pd.Series  # all problems of a row, "field: reason" joined
    stats: dict[str, RuleStats]


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value))


def _out_of_range(values: pd.Series, bounds: tuple[float, float]) -> pd.Series:
    low, high = bounds

    def check(value: Any) -> bool:
        if _is_missing(value):
            return False
        number = float(value)
        return number < low or number > high

    return values.map(check).astype(bool)


def transform_chunk(plan: TablePlan, frame: pd.DataFrame) -> Transformed:
    outputs: dict[str, RuleOutput] = {}
    stats: dict[str, RuleStats] = {}
    reason_masks: dict[str, list[tuple[str, pd.Series]]] = {r: [] for r in REASON_ORDER}

    for rule in plan.rules:  # derived rules come last, see TablePlan ordering
        if rule.derived:
            inputs = [outputs[i[len(DERIVED_PREFIX):]].values for i in rule.inputs]
        else:
            inputs = [frame[c] for c in rule.inputs]
        out = apply_steps(rule.steps, inputs)
        outputs[rule.target_field] = out
        missing = out.values.map(_is_missing)
        s = RuleStats(len(out.values), int(missing.sum()), int(out.failed.sum()), 0, list(out.step_failures))
        if rule.required:
            reason_masks["required_null"].append((rule.target_field, missing & ~out.failed))
        for kind in ("invalid_date", "invalid_number", "invalid_code", "invalid_value"):
            mask = out.failed & (out.kind == kind)
            if mask.any():
                reason_masks[kind].append((rule.target_field, mask))
        if rule.max_length is not None:
            long_text = out.values.map(lambda v: isinstance(v, str) and len(v) > rule.max_length).astype(bool)  # type: ignore[operator]
            if long_text.any():
                reason_masks["too_long"].append((rule.target_field, long_text))
        if rule.bounds is not None or rule.limit is not None:
            bounds = rule.bounds if rule.bounds is not None else (float("-inf"), float("inf"))
            bad = _out_of_range(out.values, bounds) & ~out.failed
            if rule.limit is not None:
                bad = bad | (out.values.map(lambda v: (not _is_missing(v)) and abs(float(v)) >= rule.limit).astype(bool) & ~out.failed)  # type: ignore[operator]
            s.out_of_range = int(bad.sum())
            if bad.any():
                reason_masks["out_of_range"].append((rule.target_field, bad))
        stats[rule.target_field] = s

    values = pd.DataFrame({name: out.values for name, out in outputs.items()}, index=frame.index)
    reasons = pd.Series([None] * len(frame), index=frame.index, dtype=object)
    details: list[list[str]] = [[] for _ in range(len(frame))]
    positions = {index: i for i, index in enumerate(frame.index)}
    for reason in REASON_ORDER:
        for field_name, mask in reason_masks[reason]:
            for index in mask[mask].index:
                i = positions[index]
                details[i].append(f"{field_name}: {reason}")
                if reasons.iat[i] is None:
                    reasons.iat[i] = reason
    return Transformed(values, reasons, pd.Series(["; ".join(d) if d else None for d in details], index=frame.index, dtype=object), stats)


# ------------------------------------------------------------------ text form used for staging and for comparisons


def to_text(value: Any) -> str | None:
    """The canonical text of a transformed value. The same function formats what is staged and what is read back for checks."""
    if _is_missing(value):
        return None
    if isinstance(value, bool) or isinstance(value, np.bool_):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            from datetime import UTC

            value = value.astimezone(UTC)
        return value.strftime("%Y-%m-%d %H:%M:%S+00")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return str(value)


def json_safe(frame: pd.DataFrame) -> list[str]:
    """Each source row as a JSON object text (for the quarantine table). Values that JSON cannot hold become text."""

    def convert(value: Any) -> Any:
        if _is_missing(value):
            return None
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    return [json.dumps({c: convert(v) for c, v in row.items()}, ensure_ascii=False) for row in frame.to_dict("records")]
