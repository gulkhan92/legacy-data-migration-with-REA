"""Per column statistics accumulated over chunks, then turned into a ColumnProfile.

Everything here is deterministic. Memory is bounded: distinct values and shapes are counted up to a cap,
numbers are kept as compact float arrays for exact quantiles.
"""

import re
from collections import Counter

import numpy as np
import pandas as pd

from app.profiling.models import (
    CaseStats,
    ColumnProfile,
    DateFormatStat,
    DateStats,
    LengthStats,
    NumericStats,
    PatternCount,
    ValueCount,
)
from app.profiling.readers import ColumnHint
from app.profiling.vocabulary import expanded_name

VALUE_CAP = 200_000
SHAPE_CAP = 1_000
NUMBER_CAP = 2_000_000
TYPE_SHARE = 0.9  # share of values that must read as a type (a few injected or natural defects are tolerated)
YEAR_MIN, YEAR_MAX = 1800, 2100
NUMERIC_NATIVE_KINDS = {"integer", "floating", "mixed-integer-float", "boolean"}

NUMERIC_FORMATS: dict[str, re.Pattern[str]] = {
    "plain": re.compile(r"^[+-]?\d+(\.\d+)?$"),
    "currency": re.compile(r"^([+-]?[$€£]\s?\d+(\.\d+)?|[$€£]\s?[+-]?\d+(\.\d+)?)$"),
    "thousands": re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$"),
    "decimal_comma": re.compile(r"^[+-]?\d+,\d{1,2}$"),
    "percent": re.compile(r"^[+-]?\d+(\.\d+)?%$"),
    "accounting": re.compile(r"^\(\d+(\.\d+)?\)$"),
}
CURRENCY_SYMBOLS = re.compile(r"[$€£]")

# (strptime format, shape pattern, has time)
DATE_FORMATS: list[tuple[str, re.Pattern[str], bool]] = [
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$"), False),
    ("%m/%d/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), False),
    ("%d/%m/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"), False),
    ("%Y%m%d", re.compile(r"^\d{8}$"), False),
    ("%d-%b-%y", re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{2}$"), False),
    ("%d-%b-%Y", re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"), False),
    ("%d.%m.%Y", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$"), False),
    ("%Y/%m/%d", re.compile(r"^\d{4}/\d{2}/\d{2}$"), False),
    ("%Y-%m-%dT%H:%M:%SZ", re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"), True),
    ("%Y-%m-%d %H:%M:%S", re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"), True),
]
BOOLEAN_TOKENS = {"0", "1", "true", "false", "t", "f", "y", "n", "yes", "no"}
_ALPHA = re.compile(r"[A-Za-z]")


def _luhn(text: str) -> bool:
    digits = [int(c) for c in text if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(digits) != len(re.sub(r"[ -]", "", text)):
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2)
    return total % 10 == 0


def _phone(text: str) -> bool:
    if not re.fullmatch(r"\+?[\d\s().-]{7,25}", text):
        return False
    digits = sum(c.isdigit() for c in text)
    return 7 <= digits <= 15 and bool(re.search(r"[\s().+-]", text))


SIGNAL_TESTS = {
    "email": lambda t: bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", t)),
    "ssn": lambda t: bool(re.fullmatch(r"\d{3}-\d{2}-\d{4}", t)),
    "phone": _phone,
    "uuid": lambda t: bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", t)),
    "url": lambda t: bool(re.match(r"https?://", t)),
    "ipv4": lambda t: bool(re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}", t)) and all(int(x) < 256 for x in t.split(".")),
    "payment_card": _luhn,
    "postal": lambda t: bool(re.fullmatch(r"\d{5}(-\d{4})?|\d{5}-\d{3}|\d{4,5}|[A-Za-z]\d[A-Za-z] ?\d[A-Za-z]\d|[A-Za-z]{1,2}\d[A-Za-z\d]? ?\d[A-Za-z]{2}", t)),
}


def shape(value: str) -> str:
    """Pattern class of a value: digits 9, upper A, lower a, runs of letters collapsed, long digit runs 9+."""
    chars = ["9" if ch.isdigit() else ("A" if ch.isupper() else "a") if ch.isalpha() else ch for ch in value[:64]]
    text = "".join(chars)
    text = re.sub(r"a{2,}", "a+", text)
    text = re.sub(r"A{2,}", "A+", text)
    return re.sub(r"9{7,}", "9+", text)


def parse_numeric(txt: pd.Series, masks: dict[str, pd.Series]) -> pd.Series:
    """Numbers for the values that match a numeric format, aligned with `txt` (NaN elsewhere)."""
    any_mask = pd.Series(False, index=txt.index)
    for m in masks.values():
        any_mask |= m
    sub = txt[any_mask]
    result = pd.Series(np.nan, index=txt.index, dtype=float)
    if sub.empty:
        return result
    cleaned = sub.str.replace(r"[$€£\s%]", "", regex=True)
    comma_decimal = masks["decimal_comma"][any_mask]
    cleaned = cleaned.where(~comma_decimal, cleaned.str.replace(",", ".", regex=False))
    cleaned = cleaned.str.replace(",", "", regex=False)
    accounting = masks["accounting"][any_mask]
    cleaned = cleaned.where(~accounting, "-" + cleaned.str.strip("()"))
    result.loc[sub.index] = pd.to_numeric(cleaned, errors="coerce")
    return result


class ColumnAccumulator:
    def __init__(self, name: str, hint: ColumnHint) -> None:
        self.name = name
        self.hint = hint
        self.rows = 0
        self.null = 0
        self.blank = 0
        self.whitespace = 0
        self.values: Counter[object] = Counter()
        self.approximate = False
        self.shapes: Counter[str] = Counter()
        self.lengths: Counter[int] = Counter()
        self.native = False
        self.text_seen = False
        self.numbers: list[np.ndarray] = []
        self.number_count = 0
        self.integral = True
        self.zero_count = 0
        self.minus_one_count = 0
        self.formats: Counter[str] = Counter()
        self.numeric_rows = 0
        self.symbols: set[str] = set()
        self.date_shape: Counter[str] = Counter()
        self.date_valid: Counter[str] = Counter()
        self.date_rows = 0
        self.date_ranges: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
        self.boolean_rows = 0
        self.leading_zero_rows = 0
        self.case: Counter[str] = Counter()
        self.text_min: str | None = None
        self.text_max: str | None = None

    # ---------------------------------------------------------------- update

    def update(self, series: pd.Series) -> None:
        self.rows += len(series)
        null_mask = series.isna()
        self.null += int(null_mask.sum())
        values = series[~null_mask]
        if values.empty:
            return
        kind = pd.api.types.infer_dtype(values, skipna=True)
        if self.hint.native == "int" or kind in NUMERIC_NATIVE_KINDS:
            self._update_native(values)
        else:
            self._update_text(values)

    def _remember(self, counts: pd.Series) -> None:
        for value, count in counts.items():
            if value in self.values or len(self.values) < VALUE_CAP:
                self.values[value] += int(count)
            else:
                self.approximate = True

    def _update_native(self, values: pd.Series) -> None:
        self.native = True
        numeric = pd.to_numeric(values, errors="coerce")
        arr = numeric.to_numpy(dtype=float)
        arr = arr[~np.isnan(arr)]
        self.number_count += len(arr)
        if self.number_count <= NUMBER_CAP:
            self.numbers.append(arr)
        self.integral = self.integral and bool(np.all(arr == np.floor(arr)))
        self.zero_count += int((arr == 0).sum())
        self.minus_one_count += int((arr == -1).sum())
        self._remember(numeric.value_counts())

    def _update_text(self, values: pd.Series) -> None:
        self.text_seen = True
        strs = values.astype(str)
        stripped = strs.str.strip()
        blank = stripped == ""
        self.blank += int(blank.sum())
        self.whitespace += int(((strs != stripped) & ~blank).sum())
        txt = stripped[~blank]
        if txt.empty:
            return
        self._remember(txt.value_counts())
        for pattern, count in txt.map(shape).value_counts().items():
            if pattern in self.shapes or len(self.shapes) < SHAPE_CAP:
                self.shapes[pattern] += int(count)
        self.lengths.update({int(k): int(v) for k, v in txt.str.len().value_counts().items()})
        smallest, largest = str(txt.min())[:40], str(txt.max())[:40]
        self.text_min = smallest if self.text_min is None else min(self.text_min, smallest)
        self.text_max = largest if self.text_max is None else max(self.text_max, largest)
        self._numeric_formats(txt)
        self._date_formats(txt)
        self.boolean_rows += int(txt.str.lower().isin(BOOLEAN_TOKENS).sum())
        self.leading_zero_rows += int(txt.str.match(r"^0\d+$").sum())
        self._case(txt)

    def _numeric_formats(self, txt: pd.Series) -> None:
        masks = {name: txt.str.match(rx) for name, rx in NUMERIC_FORMATS.items()}
        any_mask = pd.Series(False, index=txt.index)
        for name, mask in masks.items():
            count = int(mask.sum())
            if count:
                self.formats[name] += count
            any_mask |= mask
        self.numeric_rows += int(any_mask.sum())
        if masks["currency"].any():
            self.symbols.update(CURRENCY_SYMBOLS.findall("".join(txt[masks["currency"]].head(200))))
        numbers = parse_numeric(txt, masks).dropna().to_numpy(dtype=float)
        if len(numbers):
            self.number_count += len(numbers)
            if self.number_count <= NUMBER_CAP:
                self.numbers.append(numbers)
            self.integral = self.integral and bool(np.all(numbers == np.floor(numbers)))
            self.zero_count += int((numbers == 0).sum())

    def _date_formats(self, txt: pd.Series) -> None:
        any_valid = pd.Series(False, index=txt.index)
        for fmt, rx, _ in DATE_FORMATS:
            shaped = txt.str.match(rx)
            count = int(shaped.sum())
            if not count:
                continue
            self.date_shape[fmt] += count
            parsed = pd.to_datetime(txt[shaped], format=fmt, errors="coerce")
            ok = parsed.notna() & parsed.dt.year.between(YEAR_MIN, YEAR_MAX)
            self.date_valid[fmt] += int(ok.sum())
            any_valid.loc[ok[ok].index] = True
            if ok.any():
                low, high = parsed[ok].min(), parsed[ok].max()
                old = self.date_ranges.get(fmt)
                self.date_ranges[fmt] = (low, high) if old is None else (min(old[0], low), max(old[1], high))
        self.date_rows += int(any_valid.sum())

    def _case(self, txt: pd.Series) -> None:
        lettered = txt[txt.str.contains(_ALPHA)]
        if lettered.empty:
            return
        upper = lettered.str.isupper()
        lower = lettered.str.islower()
        first_alpha = lettered.str.extract(r"([A-Za-z])")[0]
        title = ~upper & ~lower & first_alpha.str.isupper() & ~lettered.str.contains(r"[a-z][A-Z]")
        self.case["upper"] += int(upper.sum())
        self.case["lower"] += int(lower.sum())
        self.case["title"] += int(title.sum())
        self.case["other"] += int((~upper & ~lower & ~title).sum())

    # ---------------------------------------------------------------- finalize

    def finalize(self, table: str, position: int) -> ColumnProfile:
        non_empty = self.rows - self.null - self.blank
        profile = ColumnProfile(
            table=table, name=self.name, position=position, rows=self.rows, null_count=self.null, blank_count=self.blank,
            null_rate=round((self.null + self.blank) / self.rows, 5) if self.rows else 0.0,
            distinct_count=len(self.values), distinct_approximate=self.approximate,
            unique_ratio=round(len(self.values) / non_empty, 5) if non_empty else 0.0,
            inferred_type="text", declared_type=self.hint.declared_type, layout_semantic=self.hint.layout_semantic,
            expanded_name=expanded_name(self.name),
        )  # fmt: skip
        profile.whitespace_share = round(self.whitespace / non_empty, 5) if non_empty else 0.0
        if non_empty == 0:
            profile.notes.append("The column is empty")
            return profile

        numbers = np.concatenate(self.numbers) if self.numbers else np.array([])
        if len(numbers):
            profile.numeric = NumericStats(
                parsed=self.number_count, min=float(numbers.min()), max=float(numbers.max()), mean=float(numbers.mean()),
                p01=float(np.quantile(numbers, 0.01)), p05=float(np.quantile(numbers, 0.05)),
                p95=float(np.quantile(numbers, 0.95)), p99=float(np.quantile(numbers, 0.99)),
                min_positive=float(numbers[numbers > 0].min()) if (numbers > 0).any() else None,
                negative_share=round(float((numbers < 0).mean()), 5), formats=dict(self.formats),
                currency_symbols=sorted(self.symbols), integer_valued=self.integral,
            )  # fmt: skip

        if self.native:
            profile.inferred_type = "integer" if self.integral else "decimal"
            if set(self.values) <= {0, 1, True, False} and len(self.values) == 2 and self.hint.layout_semantic in (None, "flag"):
                profile.inferred_type = "boolean"
            profile.min = _fmt_number(float(numbers.min())) if len(numbers) else None
            profile.max = _fmt_number(float(numbers.max())) if len(numbers) else None
            profile.pattern_class = "integer" if self.integral else "decimal"
        else:
            self._finalize_text(profile, non_empty)

        if not self.native:
            profile.signals = self._signals(non_empty)
        profile.top_values = [ValueCount(value=str(v)[:40], count=c) for v, c in self.values.most_common(5)]
        return profile

    def _signals(self, non_empty: int) -> dict[str, float]:
        """Share of values (by row) that look like well known kinds of identifying data."""
        hits: Counter[str] = Counter()
        for value, count in self.values.items():
            text = str(value)
            for name, test in SIGNAL_TESTS.items():
                if test(text):
                    hits[name] += count
        return {name: round(count / non_empty, 4) for name, count in hits.items() if count}

    def _finalize_text(self, profile: ColumnProfile, non_empty: int) -> None:
        text_rows = sum(self.shapes.values())
        profile.top_patterns = [PatternCount(pattern=p, count=c, share=round(c / text_rows, 4)) for p, c in self.shapes.most_common(5)]
        profile.pattern_class = profile.top_patterns[0].pattern if profile.top_patterns else None
        profile.min, profile.max = self.text_min, self.text_max
        if self.lengths:
            total = sum(self.lengths.values())
            running, p95 = 0, max(self.lengths)
            for length in sorted(self.lengths):
                running += self.lengths[length]
                if running >= 0.95 * total:
                    p95 = length
                    break
            profile.length = LengthStats(min=min(self.lengths), max=max(self.lengths),
                                         mean=round(sum(k * v for k, v in self.lengths.items()) / total, 2), p95=p95)  # fmt: skip
        letters = sum(self.case.values())
        if letters:
            profile.case = CaseStats(**{k: round(self.case[k] / letters, 4) for k in ("upper", "lower", "title", "other")}, letters=letters)

        date_share = self.date_rows / non_empty
        numeric_share = self.numeric_rows / non_empty
        if date_share >= TYPE_SHARE:
            profile.date = self._date_stats(non_empty)
            profile.inferred_type = "timestamp" if profile.date.has_time else "date"
            profile.min = profile.date.min or profile.min
            profile.max = profile.date.max or profile.max
        elif numeric_share >= TYPE_SHARE and profile.numeric and not self._is_code(profile, non_empty):
            only_plain_integers = self.integral and not self.symbols and set(self.formats) <= {"plain"}
            profile.inferred_type = "integer" if only_plain_integers else "decimal"
            profile.min, profile.max = _fmt_number(profile.numeric.min), _fmt_number(profile.numeric.max)
        elif self.boolean_rows / non_empty >= TYPE_SHARE and len(self.values) <= 3:
            profile.inferred_type = "boolean"
        else:
            if self.date_shape:
                profile.date = self._date_stats(non_empty)
                profile.notes.append("Some values look like dates but not enough to call the column a date")

    def _is_code(self, profile: ColumnProfile, non_empty: int) -> bool:
        """Digits that are an identifier or code, not a quantity: leading zeros, or a code, zip or phone column."""
        if self.leading_zero_rows / non_empty > 0.01:
            return True
        return bool(set(profile.expanded_name.split()) & {"code", "zip", "postal", "phone", "fax", "ssn", "sku"})

    def _date_stats(self, non_empty: int) -> DateStats:
        formats = {f: DateFormatStat(shape=self.date_shape[f], valid=self.date_valid[f]) for f in self.date_shape}
        dominant = [f for f in sorted(formats, key=lambda f: -formats[f].valid) if formats[f].valid >= max(2, 0.02 * non_empty)]
        ambiguous = False
        # Day first and month first share a shape. Keep the better one; a tie is reported and month first is used.
        both = [f for f in ("%m/%d/%Y", "%d/%m/%Y") if f in dominant]
        if len(both) == 2:
            mdy, dmy = formats["%m/%d/%Y"].valid, formats["%d/%m/%Y"].valid
            keep = "%m/%d/%Y" if mdy >= dmy else "%d/%m/%Y"
            ambiguous = min(mdy, dmy) == max(mdy, dmy)
            dominant.remove("%d/%m/%Y" if keep == "%m/%d/%Y" else "%m/%d/%Y")
        has_time = any(f in dominant and t for f, _, t in DATE_FORMATS)
        ranges = [self.date_ranges[f] for f in dominant if f in self.date_ranges]  # only the formats that were accepted
        date_min = min(r[0] for r in ranges) if ranges else None
        date_max = max(r[1] for r in ranges) if ranges else None
        return DateStats(
            formats=formats, dominant=dominant, ambiguous=ambiguous, has_time=has_time,
            min=date_min.isoformat()[:19] if date_min is not None else None,
            max=date_max.isoformat()[:19] if date_max is not None else None,
        )  # fmt: skip


def _fmt_number(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else f"{value:.6g}"
