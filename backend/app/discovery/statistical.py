"""Statistical layout inference, the fallback when no trustworthy analysis result is available.

Used when the assembly is missing, cannot be analysed, or produced a layout that failed validation. It looks
only at the bytes of the data file: the table directory (or, failing that, the record size that makes the
columns of the file most regular), then the byte class and entropy at every offset of a record. The result
is always marked as lower confidence: boundaries between adjacent text fields and between adjacent integers
are guesses, and field names are generated.
"""

import math
import struct
from collections import Counter

from app.discovery.binary_structure import DirectoryProbe, describe_header, find_directory
from app.discovery.models import FieldLayout, LayoutSpec, RecordLayout

MAX_CONFIDENCE = 0.6
SAMPLE_ROWS = 1000
HEADER_CANDIDATES = (0, 16, 32, 64, 96, 128)


def _rows(blob: bytes, data_offset: int, record_size: int, count: int) -> list[bytes]:
    step = max(1, count // SAMPLE_ROWS)
    return [blob[data_offset + i * record_size : data_offset + (i + 1) * record_size] for i in range(0, count, step)]


def _entropy(values: list[int]) -> float:
    total = len(values)
    return -sum(c / total * math.log2(c / total) for c in Counter(values).values()) if total else 0.0


class Column:
    def __init__(self, values: list[int]) -> None:
        n = len(values) or 1
        self.printable = sum(1 for v in values if 0x20 <= v < 0x7F or v >= 0xA0) / n
        self.space = sum(1 for v in values if v == 0x20) / n
        self.zero = sum(1 for v in values if v == 0) / n
        self.entropy = _entropy(values)

    @property
    def kind(self) -> str:
        if self.zero >= 0.97:
            return "zero"
        if self.printable >= 0.9:
            return "text"
        return "binary"


def infer_record_size(blob: bytes) -> tuple[int, int] | None:
    """(header size, record size) that make the byte columns most regular, or None."""
    results: list[tuple[float, int, int]] = []
    for header in HEADER_CANDIDATES:
        body = len(blob) - header
        for size in range(8, 513):
            if body % size or body // size < 8:
                continue
            count = body // size
            rows = _rows(blob, header, size, count)
            score = sum(Counter(row[i] for row in rows).most_common(1)[0][1] / len(rows) for i in range(size)) / size
            results.append((score, header, size))
    if not results:
        return None
    top = max(results)[0]
    # Prefer the smallest record size that is nearly as regular as the best one.
    best = min((r for r in results if r[0] >= top * 0.97), key=lambda r: (r[2], r[1]))
    return best[1], best[2]


def _segment_text(columns: list[Column], start: int, end: int) -> list[tuple[int, int]]:
    """Split a run of text columns where padding (spaces) stops and a new value starts."""
    cuts = [start]
    for i in range(start + 1, end):
        if columns[i].space <= 0.1 and columns[i - 1].space >= 0.5:
            cuts.append(i)
    cuts.append(end)
    return list(zip(cuts[:-1], cuts[1:], strict=True))


def _segment_binary(columns: list[Column], start: int, end: int) -> list[tuple[int, int]]:
    """Split a run of non-text columns: a little endian integer ends with zero bytes, the next one starts."""
    cuts = [start]
    for i in range(start + 1, end):
        if columns[i - 1].kind == "zero" and columns[i].kind == "binary":
            cuts.append(i)
    cuts.append(end)
    spans: list[tuple[int, int]] = []
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        length = b - a
        position = a
        while length > 0:
            unit = 8 if length >= 8 and length % 8 == 0 else 4 if length >= 4 else 2 if length >= 2 else 1
            spans.append((position, position + unit))
            position += unit
            length -= unit
    return spans


def _int_field(columns: list[Column], rows: list[bytes], a: int, b: int) -> FieldLayout:
    length = b - a
    type_name = {1: "uint8", 2: "uint16", 4: "int32", 8: "int64"}[length]
    endianness = None
    if length > 1:
        first, last = columns[a], columns[b - 1]
        endianness = "big" if first.zero > last.zero + 0.3 else "little"
    semantic = None
    if length == 4:
        fmt = ">i" if endianness == "big" else "<i"
        values = [struct.unpack_from(fmt, row, a)[0] for row in rows]
        if sum(1 for v in values if 1 <= v < 80_000 or v in (0, -1)) / len(values) > 0.98 and len(set(values)) > 20:
            semantic = "epoch_days?"
        elif values[: min(20, len(values))] == list(range(values[0], values[0] + min(20, len(values)))):
            semantic = "key?"
    return FieldLayout(name=f"f{a:03d}", offset=a, length=length, type=type_name, endianness=endianness, semantic=semantic,
                       confidence=0.45, method="statistical", name_source="generated")  # fmt: skip


def infer_table(blob: bytes, name: str, data_offset: int, record_size: int, count: int, table_id: int | None = None) -> RecordLayout:
    rows = _rows(blob, data_offset, record_size, count)
    columns = [Column([row[i] for row in rows]) for i in range(record_size)]
    fields: list[FieldLayout] = []
    i = 0
    while i < record_size:
        kind = "text" if columns[i].kind == "text" else "other"
        j = i
        while j < record_size and (("text" if columns[j].kind == "text" else "other") == kind):
            j += 1
        if kind == "text":
            for a, b in _segment_text(columns, i, j):
                fields.append(FieldLayout(name=f"f{a:03d}", offset=a, length=b - a, type="char", encoding="cp1252",
                                          confidence=0.55 if (b - a) >= 3 else 0.35, method="statistical", name_source="generated"))  # fmt: skip
        else:
            for a, b in _segment_binary(columns, i, j):
                fields.append(_int_field(columns, rows, a, b))
        i = j
    return RecordLayout(name=name, table_id=table_id, record_size=record_size, record_count=count, data_offset=data_offset,
                        fields=fields, confidence=min(MAX_CONFIDENCE, sum(f.confidence for f in fields) / max(1, len(fields))))  # fmt: skip


def infer_layout(blob: bytes, probe: DirectoryProbe | None = None) -> LayoutSpec:
    probe = probe or find_directory(blob)
    limitations = ["Field names are generated and boundaries between adjacent values are estimates"]
    if probe is not None:
        header = describe_header(blob, probe)
        tables = [infer_table(blob, f"table_{e.table_id}", e.data_offset, e.record_size, e.record_count, e.table_id) for e in probe.entries]
        magic = probe.magic
    else:
        guess = infer_record_size(blob)
        if guess is None:
            return LayoutSpec(method="statistical", confidence=0.0, validated=False, file_size=len(blob),
                              limitations=[*limitations, "No record structure could be found"])  # fmt: skip
        header_size, record_size = guess
        tables = [infer_table(blob, "table_1", header_size, record_size, (len(blob) - header_size) // record_size)]
        header = None
        magic = None
        limitations.append("No table directory was found; the record size was inferred from byte regularity")
    confidence = min(MAX_CONFIDENCE, sum(t.confidence for t in tables) / max(1, len(tables)))
    return LayoutSpec(format=magic, method="statistical", confidence=round(confidence, 3), validated=False, file_size=len(blob),
                      header=header, tables=tables, limitations=limitations)  # fmt: skip
