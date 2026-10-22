"""The transform DSL: a whitelist of operations, each with validated parameters. There is no eval, no expression language and no
way to name a function: a rule is data, `{"op": "parse_date", "formats": [...]}`, and only the operations in OPS exist.

A rule reads one or more source columns and runs its steps in order. A value that is missing stays missing. A value that cannot be
converted becomes missing and is counted as a failure of that step with a kind (invalid_date, invalid_number, invalid_code,
invalid_value), so a dry run can report failures per rule and a load can quarantine the row with the right reason.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

YEAR_MIN, YEAR_MAX = 1800, 2100
MAX_REGEX_LENGTH = 200
MAX_REGEX_INPUT = 500
MAX_MAPPING_ENTRIES = 500
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")
BACKREFERENCE = re.compile(r"\\[1-9]|\(\?P=")
STYLE_NONE = "none"


class RuleError(ValueError):
    """A rule that is not valid: unknown operation, wrong parameters, wrong number of inputs, unsafe pattern."""


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ parameters per operation


class NoParams(Params):
    pass


class ToText(Params):
    null_values: list[int] = []  # numbers that mean "no value" in the legacy system, such as 0 for "no reference"


class Trim(Params):
    collapse_spaces: bool = False


class Case(Params):
    mode: Literal["lower", "upper", "title", "capitalize"]
    # Only fix values written in these styles. "inverted" is a value that starts in lower case and has capitals after it
    # (sMITH), which is how swapped case looks. Natural mixed case such as McDonald is "other" and is never touched by a fix.
    styles: list[Literal["upper", "lower", "inverted", "title", "other"]] | None = None
    min_length: int = Field(4, ge=1, le=20)  # all capital values shorter than this (USA, UK) are taken to be acronyms


class CastInt(Params):
    null_values: list[int] = []


class CastDecimal(Params):
    thousands: str | None = ","
    decimal: Literal[".", ","] = "."
    scale: int | None = Field(2, ge=0, le=8)


class CentsToDecimal(Params):
    scale: int = Field(2, ge=0, le=8)


class ParseDate(Params):
    formats: list[str] = Field(min_length=1, max_length=12)

    @field_validator("formats")
    @classmethod
    def _formats(cls, value: list[str]) -> list[str]:
        for f in value:
            if not re.fullmatch(r"[%A-Za-z0-9 ./:\-,TZ]+", f) or "%" not in f:
                raise ValueError(f"not a date format: {f}")
        return value


class Epoch(Params):
    epoch: date = date(1900, 1, 1)
    null_values: list[int] = [0]
    invalid_values: list[int] = [-1]


class NormalizePhone(Params):
    pass


class MapCodes(Params):
    mapping: dict[str, str]
    default: str | None = None
    passthrough: bool = False  # keep codes that are not in the map (as text) instead of failing

    @field_validator("mapping")
    @classmethod
    def _size(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_MAPPING_ENTRIES:
            raise ValueError(f"at most {MAX_MAPPING_ENTRIES} codes")
        return value


class Split(Params):
    delimiter: str = Field(min_length=1, max_length=10)
    index: int = Field(0, ge=0, le=20)


class Concat(Params):
    separator: str = Field(" ", max_length=10)
    skip_null: bool = True


class Default(Params):
    value: str | int | float | bool


class RegexExtract(Params):
    pattern: str = Field(min_length=1, max_length=MAX_REGEX_LENGTH)
    group: int = Field(1, ge=0, le=9)

    @field_validator("pattern")
    @classmethod
    def _safe(cls, value: str) -> str:
        validate_pattern(value)
        return value


class BitFlag(Params):
    bit: int = Field(ge=0, le=63)
    true_value: str | bool
    false_value: str | bool


class ToBoolean(Params):
    true_values: list[str] = ["1", "true", "t", "yes", "y"]
    false_values: list[str] = ["0", "false", "f", "no", "n"]


def validate_pattern(pattern: str) -> re.Pattern[str]:
    """Reject patterns that are too long, do not compile, use backreferences or nest quantifiers (catastrophic backtracking)."""
    if len(pattern) > MAX_REGEX_LENGTH:
        raise ValueError("the pattern is too long")
    if NESTED_QUANTIFIER.search(pattern) or BACKREFERENCE.search(pattern):
        raise ValueError("the pattern could backtrack without limit (nested quantifier or backreference)")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"the pattern does not compile: {exc}") from exc


# ------------------------------------------------------------------ results


@dataclass
class StepResult:
    values: pd.Series
    failed: pd.Series  # True where a value existed but could not be converted
    kind: str | None = None


def _object(values: list[Any], index: pd.Index) -> pd.Series:
    return pd.Series(values, index=index, dtype=object)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA or (isinstance(value, str) and value.strip() == "")


def map_values(series: pd.Series, convert: Callable[[Any], Any], kind: str) -> StepResult:
    """Apply `convert` to every distinct value once. A missing value stays missing. `convert` raises ValueError to fail."""
    cache: dict[Any, tuple[bool, Any]] = {}
    out: list[Any] = []
    failed: list[bool] = []
    for value in series.tolist():
        if _blank(value):
            out.append(None)
            failed.append(False)
            continue
        key = value if isinstance(value, (str, int, float, bool, bytes)) else repr(value)
        if key not in cache:
            try:
                cache[key] = (True, convert(value))
            except (ValueError, InvalidOperation, OverflowError, TypeError):
                cache[key] = (False, None)
        ok, result = cache[key]
        out.append(result if ok else None)
        failed.append(not ok)
    return StepResult(_object(out, series.index), pd.Series(failed, index=series.index), kind)


def _text(value: Any) -> str:
    if isinstance(value, (float, np.floating)) and float(value) == int(value):
        return str(int(value))
    if isinstance(value, (np.integer,)):
        return str(int(value))
    return str(value)


# ------------------------------------------------------------------ operations


def op_to_text(series: pd.Series, p: ToText) -> StepResult:
    nulls = set(p.null_values)

    def convert(value: Any) -> str:
        if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) and float(value) in nulls:
            raise ValueError("null marker")
        return _text(value)

    result = map_values(series, convert, "invalid_value")
    result.failed[:] = False  # a null marker is a missing value, not a failure
    return result


def op_trim(series: pd.Series, p: Trim) -> StepResult:
    def convert(value: Any) -> str:
        text = _text(value).strip()
        if p.collapse_spaces:
            text = re.sub(r"\s+", " ", text)
        if not text:
            raise ValueError("blank")
        return text

    result = map_values(series, convert, "invalid_value")
    result.failed[:] = False
    return result


def _style(text: str) -> str:
    if not re.search(r"[A-Za-z]", text):
        return STYLE_NONE
    if text.isupper():
        return "upper"
    if text.islower():
        return "lower"
    first = re.search(r"[A-Za-z]", text)
    if first and first.group().isupper() and not re.search(r"[a-z][A-Z]", text):
        return "title"
    if first and first.group().islower() and re.search(r"[A-Z]", text):
        return "inverted"
    return "other"


def op_case(series: pd.Series, p: Case) -> StepResult:
    def convert(value: Any) -> str:
        text = _text(value)
        if p.styles is not None:
            style = _style(text)
            letters = sum(ch.isalpha() for ch in text)
            if style not in p.styles or (style == "upper" and letters < p.min_length):
                return text
        if p.mode == "lower":
            return text.lower()
        if p.mode == "upper":
            return text.upper()
        if p.mode == "capitalize":
            return text[:1].upper() + text[1:].lower()
        return " ".join(w[:1].upper() + w[1:].lower() for w in text.split(" "))

    return map_values(series, convert, "invalid_value")


def op_cast_int(series: pd.Series, p: CastInt) -> StepResult:
    nulls = set(p.null_values)

    def convert(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("boolean")
        number = Decimal(_text(value).strip().replace(",", "")) if isinstance(value, str) else Decimal(str(value))
        if number != number.to_integral_value():
            raise ValueError("not a whole number")
        if int(number) in nulls:
            raise KeyError  # handled below as a missing value
        return int(number)

    def safe(value: Any) -> Any:
        try:
            return convert(value)
        except KeyError:
            return _NULL

    result = map_values(series, safe, "invalid_number")
    missing = result.values.map(lambda v: v is _NULL)
    result.values[missing] = None
    result.failed[missing] = False
    return result


_NULL = object()


_SYMBOLS = re.compile(r"[$€£¥\s%]")


def op_cast_decimal(series: pd.Series, p: CastDecimal) -> StepResult:
    quantum = Decimal(1).scaleb(-p.scale) if p.scale is not None else None

    def convert(value: Any) -> Decimal:
        if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
            number = Decimal(str(value))
        else:
            text = _SYMBOLS.sub("", _text(value))
            negative = text.startswith("(") and text.endswith(")")
            text = text.strip("()")
            if p.thousands:
                text = text.replace(p.thousands, "")
            if p.decimal == ",":
                text = text.replace(",", ".")
            if not re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
                raise ValueError("not a number")
            number = Decimal(text)
            number = -number if negative else number
        return number.quantize(quantum, rounding=ROUND_HALF_UP) if quantum is not None else number

    return map_values(series, convert, "invalid_number")


def op_cents_to_decimal(series: pd.Series, p: CentsToDecimal) -> StepResult:
    def convert(value: Any) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("boolean")
        return (Decimal(str(int(Decimal(str(value))))) / 100).quantize(Decimal(1).scaleb(-p.scale), rounding=ROUND_HALF_UP)

    return map_values(series, convert, "invalid_number")


def _in_years(parsed: datetime | date) -> bool:
    return YEAR_MIN <= parsed.year <= YEAR_MAX


def op_parse_date(series: pd.Series, p: ParseDate) -> StepResult:
    def convert(value: Any) -> date:
        text = _text(value).strip()
        for fmt in p.formats:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if _in_years(parsed):
                return parsed.date()
        raise ValueError("no format fits")

    return map_values(series, convert, "invalid_date")


def op_parse_timestamp(series: pd.Series, p: ParseDate) -> StepResult:
    def convert(value: Any) -> datetime:
        text = _text(value).strip()
        for fmt in p.formats:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if _in_years(parsed):
                return parsed.replace(tzinfo=UTC)
        raise ValueError("no format fits")

    return map_values(series, convert, "invalid_date")


def _epoch(series: pd.Series, p: Epoch, minutes: bool) -> StepResult:
    nulls, invalid = set(p.null_values), set(p.invalid_values)
    origin = datetime.combine(p.epoch, datetime.min.time(), tzinfo=UTC)

    def convert(value: Any) -> Any:
        number = int(Decimal(str(value)))
        if number in nulls:
            return _NULL
        if number in invalid:
            raise ValueError("unreadable date marker")
        moment = origin + (timedelta(minutes=number) if minutes else timedelta(days=number))
        if not _in_years(moment):
            raise ValueError("date out of range")
        return moment if minutes else moment.date()

    result = map_values(series, convert, "invalid_date")
    missing = result.values.map(lambda v: v is _NULL)
    result.values[missing] = None
    return result


def op_epoch_days_to_date(series: pd.Series, p: Epoch) -> StepResult:
    return _epoch(series, p, minutes=False)


def op_epoch_minutes_to_timestamp(series: pd.Series, p: Epoch) -> StepResult:
    return _epoch(series, p, minutes=True)


_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212"), "-")


def op_normalize_phone(series: pd.Series, p: NormalizePhone) -> StepResult:
    """A light clean up that keeps the number as written: control characters out, unusual dashes and spaces made plain, runs of
    spaces collapsed. It does not guess country codes or drop trunk prefixes."""

    def convert(value: Any) -> str:
        text = "".join(ch for ch in _text(value) if unicodedata.category(ch)[0] != "C")
        text = re.sub(r"\s+", " ", text.translate(_DASHES).replace("\u00a0", " ")).strip()
        if not text:
            raise ValueError("blank")
        return text

    result = map_values(series, convert, "invalid_value")
    result.failed[:] = False
    return result


def op_map_codes(series: pd.Series, p: MapCodes) -> StepResult:
    def convert(value: Any) -> str:
        key = _text(value).strip()
        if key in p.mapping:
            return p.mapping[key]
        if p.default is not None:
            return p.default
        if p.passthrough:
            return key
        raise ValueError("unknown code")

    return map_values(series, convert, "invalid_code")


def op_split(series: pd.Series, p: Split) -> StepResult:
    def convert(value: Any) -> str:
        parts = _text(value).split(p.delimiter)
        part = parts[p.index].strip() if p.index < len(parts) else ""
        if not part:
            raise KeyError
        return part

    result = map_values(series, lambda v: _NULL if _try_missing(convert, v) else convert(v), "invalid_value")
    missing = result.values.map(lambda v: v is _NULL)
    result.values[missing] = None
    return result


def _try_missing(fn: Callable[[Any], Any], value: Any) -> bool:
    try:
        fn(value)
    except KeyError:
        return True
    return False


def op_default(series: pd.Series, p: Default) -> StepResult:
    values = series.map(lambda v: p.value if _blank(v) else v).astype(object)
    return StepResult(values, pd.Series(False, index=series.index), None)


def op_regex_extract(series: pd.Series, p: RegexExtract) -> StepResult:
    pattern = validate_pattern(p.pattern)

    def convert(value: Any) -> str:
        match = pattern.search(_text(value)[:MAX_REGEX_INPUT])
        if not match:
            raise ValueError("no match")
        return match.group(p.group)

    return map_values(series, convert, "invalid_value")


def op_bit_flag(series: pd.Series, p: BitFlag) -> StepResult:
    return map_values(series, lambda v: p.true_value if (int(Decimal(str(v))) >> p.bit) & 1 else p.false_value, "invalid_value")


def op_to_boolean(series: pd.Series, p: ToBoolean) -> StepResult:
    def convert(value: Any) -> bool:
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        text = _text(value).strip().lower()
        if text in p.true_values:
            return True
        if text in p.false_values:
            return False
        raise ValueError("not a boolean")

    return map_values(series, convert, "invalid_value")


def op_is_not_null(series: pd.Series, p: NoParams) -> StepResult:
    return StepResult(series.map(lambda v: not _blank(v)).astype(object), pd.Series(False, index=series.index), None)


def op_concat(series_list: list[pd.Series], p: Concat) -> StepResult:
    index = series_list[0].index
    out = []
    for row in zip(*(s.tolist() for s in series_list), strict=True):
        parts = [_text(v).strip() for v in row if not _blank(v)]
        if not p.skip_null and len(parts) != len(row):
            out.append(None)
        else:
            out.append(p.separator.join(parts) or None)
    return StepResult(_object(out, index), pd.Series(False, index=index), None)


@dataclass(frozen=True)
class OpSpec:
    name: str
    params: type[Params]
    func: Callable[..., StepResult]
    min_inputs: int = 1
    max_inputs: int = 1
    produces: str = "any"  # the kind of value the step leaves: text, integer, decimal, date, timestamp, boolean, any


OPS: dict[str, OpSpec] = {
    "to_text": OpSpec("to_text", ToText, op_to_text, produces="text"),
    "trim": OpSpec("trim", Trim, op_trim, produces="text"),
    "case": OpSpec("case", Case, op_case, produces="text"),
    "cast_int": OpSpec("cast_int", CastInt, op_cast_int, produces="integer"),
    "cast_decimal": OpSpec("cast_decimal", CastDecimal, op_cast_decimal, produces="decimal"),
    "cents_to_decimal": OpSpec("cents_to_decimal", CentsToDecimal, op_cents_to_decimal, produces="decimal"),
    "parse_date": OpSpec("parse_date", ParseDate, op_parse_date, produces="date"),
    "parse_timestamp": OpSpec("parse_timestamp", ParseDate, op_parse_timestamp, produces="timestamp"),
    "epoch_days_to_date": OpSpec("epoch_days_to_date", Epoch, op_epoch_days_to_date, produces="date"),
    "epoch_minutes_to_timestamp": OpSpec("epoch_minutes_to_timestamp", Epoch, op_epoch_minutes_to_timestamp, produces="timestamp"),
    "normalize_phone": OpSpec("normalize_phone", NormalizePhone, op_normalize_phone, produces="text"),
    "map_codes": OpSpec("map_codes", MapCodes, op_map_codes, produces="text"),
    "split": OpSpec("split", Split, op_split, produces="text"),
    "concat": OpSpec("concat", Concat, op_concat, min_inputs=2, max_inputs=10, produces="text"),
    "default": OpSpec("default", Default, op_default),
    "regex_extract": OpSpec("regex_extract", RegexExtract, op_regex_extract, produces="text"),
    "bit_flag": OpSpec("bit_flag", BitFlag, op_bit_flag),
    "to_boolean": OpSpec("to_boolean", ToBoolean, op_to_boolean, produces="boolean"),
    "is_not_null": OpSpec("is_not_null", NoParams, op_is_not_null, produces="boolean"),
}


# ------------------------------------------------------------------ rules


@dataclass
class ParsedStep:
    spec: OpSpec
    params: Params
    raw: dict[str, Any]


def parse_steps(steps: list[dict[str, Any]], n_inputs: int = 1) -> list[ParsedStep]:
    """Validate a list of steps. Raises RuleError with a message that says which step is wrong and why."""
    if not isinstance(steps, list) or not steps:
        raise RuleError("a rule needs at least one step")
    if len(steps) > 12:
        raise RuleError("a rule may have at most 12 steps")
    parsed: list[ParsedStep] = []
    for position, step in enumerate(steps):
        if not isinstance(step, dict) or "op" not in step:
            raise RuleError(f"step {position + 1} must be an object with an op")
        name = step["op"]
        spec = OPS.get(name)
        if spec is None:
            raise RuleError(f"step {position + 1}: unknown operation {name!r}; allowed: {', '.join(sorted(OPS))}")
        arguments = {k: v for k, v in step.items() if k != "op"}
        try:
            params = spec.params.model_validate(arguments)
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'parameters'}: {e['msg']}" for e in exc.errors()[:3])
            raise RuleError(f"step {position + 1} ({name}): {detail}") from None
        if position == 0:
            if not spec.min_inputs <= n_inputs <= spec.max_inputs:
                raise RuleError(f"step 1 ({name}) takes {spec.min_inputs} to {spec.max_inputs} input columns, the rule has {n_inputs}")
        elif spec.max_inputs > 1:
            raise RuleError(f"step {position + 1} ({name}) can only be the first step")
        parsed.append(ParsedStep(spec, params, step))
    if n_inputs > 1 and parsed[0].spec.max_inputs == 1:
        raise RuleError("a rule with several input columns must start with concat")
    return parsed


@dataclass
class RuleOutput:
    values: pd.Series
    failed: pd.Series  # any step failed for this value
    kind: pd.Series  # failure kind per row (None where nothing failed)
    step_failures: list[int] = field(default_factory=list)  # failed values per step


def apply_steps(steps: list[ParsedStep], inputs: list[pd.Series]) -> RuleOutput:
    index = inputs[0].index
    failed = pd.Series(False, index=index)
    kind = pd.Series([None] * len(index), index=index, dtype=object)
    counts: list[int] = []
    current: Any = inputs if steps[0].spec.max_inputs > 1 else inputs[0]
    for step in steps:
        result = step.spec.func(current, step.params)
        new = result.failed & ~failed  # the first failure of a row names its kind
        if new.any():
            kind[new] = result.kind
        failed = failed | result.failed
        counts.append(int(result.failed.sum()))
        current = result.values
    return RuleOutput(current, failed, kind, counts)
