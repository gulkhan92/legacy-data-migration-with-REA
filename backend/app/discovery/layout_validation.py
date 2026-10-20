"""Validate a recovered layout against the data file itself, without trusting the analysis that produced it.

Checks:
  * the size rule: header + sum(record_count * record_size) must equal the file size
  * every table: the layout's record size and count agree with the directory
  * the first and last records: fixed text decodes to printable text with trailing padding only, fields
    that look like dates hold plausible day or minute counts, and key fields count up from one
"""

import struct
from dataclasses import dataclass

from app.discovery.binary_structure import DirectoryEntry, DirectoryProbe, computed_size
from app.discovery.models import FieldCheck, FieldLayout, RecordLayout, ValidationReport

SAMPLE_HEAD = 200
SAMPLE_TAIL = 50
PASS_RATIO = 0.95

# Plausible stored values for fields the application marks as dates.
DAY_LIMIT = 80_000  # about the year 2119 counted from 1900
MINUTE_LIMIT = 120_000_000  # about the year 2128 counted from 1900
INVALID_SENTINEL = -1  # an unreadable date marker is data, not a layout error

FORMATS = {
    ("int8", None): "<b", ("uint8", None): "<B",
    ("int16", "little"): "<h", ("int16", "big"): ">h", ("uint16", "little"): "<H", ("uint16", "big"): ">H",
    ("int32", "little"): "<i", ("int32", "big"): ">i", ("uint32", "little"): "<I", ("uint32", "big"): ">I",
    ("int64", "little"): "<q", ("int64", "big"): ">q", ("uint64", "little"): "<Q", ("uint64", "big"): ">Q",
}  # fmt: skip


@dataclass
class FieldStats:
    checked: int = 0
    passed: int = 0


def decode_number(raw: bytes, field: FieldLayout) -> int | None:
    fmt = FORMATS.get((field.type, field.endianness if field.length > 1 else None)) or FORMATS.get((field.type, "little"))
    if fmt is None or struct.calcsize(fmt) != len(raw):
        return None
    return struct.unpack(fmt, raw)[0]


def _printable_text(raw: bytes, field: FieldLayout) -> bool:
    try:
        text = raw.decode(field.encoding or "cp1252")
    except (UnicodeDecodeError, LookupError):
        return False
    body = text.rstrip(" \x00")
    return body.isprintable() and "\x00" not in body


def _sample_rows(count: int) -> list[int]:
    head = list(range(min(count, SAMPLE_HEAD)))
    tail = list(range(max(len(head), count - SAMPLE_TAIL), count))
    return head + tail


def validate_layout(blob: bytes, probe: DirectoryProbe | None, tables: list[RecordLayout]) -> tuple[ValidationReport, dict[tuple[str, str], FieldCheck]]:
    report = ValidationReport(file_size=len(blob))
    checks: dict[tuple[str, str], FieldCheck] = {}
    if probe is None:
        report.problems.append("No table directory was found, so the size rule cannot be checked")
        return report, checks

    report.computed_size = computed_size(probe)
    report.size_rule_ok = report.computed_size == len(blob)
    if not report.size_rule_ok:
        report.problems.append(f"header + records is {report.computed_size} bytes, the file is {len(blob)}")

    entries: list[DirectoryEntry] = probe.entries
    report.tables_matched = len(entries) == len(tables) and all(
        e.record_size == t.record_size and e.record_count == t.record_count and e.data_offset == t.data_offset
        for e, t in zip(entries, tables, strict=False)
    )
    if not report.tables_matched:
        report.problems.append("The layout tables do not match the directory (record size, count or offset)")

    for table in tables:
        field_sum = sum(f.length for f in table.fields)
        if field_sum != table.record_size:
            report.problems.append(f"{table.name}: fields add up to {field_sum} bytes, the record is {table.record_size}")
            continue
        stats: dict[str, FieldStats] = {f.name: FieldStats() for f in table.fields}
        for row in _sample_rows(table.record_count):
            base = table.data_offset + row * table.record_size
            if base + table.record_size > len(blob):
                report.problems.append(f"{table.name}: record {row} is outside the file")
                break
            report.records_checked += 1
            for f in table.fields:
                raw = blob[base + f.offset : base + f.offset + f.length]
                ok = _check_field(raw, f, row)
                if ok is None:
                    continue
                stats[f.name].checked += 1
                stats[f.name].passed += int(ok)
        for f in table.fields:
            s = stats[f.name]
            checks[(table.name, f.name)] = FieldCheck(table=table.name, field=f.name, checked=s.checked, passed=s.passed)

    bad = [c for c in checks.values() if c.checked and c.ratio < PASS_RATIO]
    for check in bad:
        report.problems.append(f"{check.table}.{check.field}: {check.passed} of {check.checked} sampled values look wrong")
    report.field_checks = list(checks.values())
    report.passed = report.size_rule_ok and report.tables_matched and not bad and not any(p for p in report.problems if "outside" in p or "add up" in p)
    return report, checks


def _check_field(raw: bytes, f: FieldLayout, row: int) -> bool | None:
    """True/False when the value can be judged, None when nothing is known about the field."""
    if f.type == "char":
        return _printable_text(raw, f)
    value = decode_number(raw, f)
    if value is None:
        return None
    if f.semantic == "epoch_days":
        return value in (0, INVALID_SENTINEL) or 0 < value < DAY_LIMIT
    if f.semantic == "epoch_minutes":
        return value in (0, INVALID_SENTINEL) or 0 < value < MINUTE_LIMIT
    if f.semantic == "key":
        return value == row + 1
    return None
