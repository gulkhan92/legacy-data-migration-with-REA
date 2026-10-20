"""Structural analysis of a binary data file that needs no knowledge of the application.

Many record files start with a header and a table directory. This module looks for a directory whose
entries (table id, record size, record count, data offset) chain together so that

    header + sum(record_count * record_size) == file size

which is strong evidence on its own, because random bytes essentially never satisfy it. The directory
gives the record size and count of every table, and the exact offset where each table starts.
"""

import struct
from dataclasses import dataclass, field

from app.discovery.models import FieldLayout, HeaderLayout

CODE_PAGES = {437, 850, 852, 1250, 1251, 1252, 1253, 1254, 1255, 1256, 1257, 1258, 65001}
ENTRY_SIZES = (12, 16, 20, 24, 32)
MAX_TABLES = 32


@dataclass
class DirectoryEntry:
    table_id: int
    record_size: int
    record_count: int
    data_offset: int


@dataclass
class DirectoryProbe:
    directory_offset: int
    entry_size: int
    entries: list[DirectoryEntry]
    header_size: int  # where the first table starts
    magic: str | None = None
    notes: list[str] = field(default_factory=list)


def computed_size(probe: DirectoryProbe) -> int:
    return probe.header_size + sum(e.record_count * e.record_size for e in probe.entries)


def find_directory(blob: bytes) -> DirectoryProbe | None:
    """Find a directory whose entries chain to the end of the file. Returns None when there is none."""
    size = len(blob)
    for directory_offset in range(0, 129, 4):
        for entry_size in ENTRY_SIZES:
            for count in range(1, MAX_TABLES + 1):
                end = directory_offset + count * entry_size
                if end > min(size, 4096):
                    break
                entries = _parse_entries(blob, directory_offset, entry_size, count)
                if entries is None or not _chains(entries, end, size):
                    continue
                probe = DirectoryProbe(
                    directory_offset=directory_offset,
                    entry_size=entry_size,
                    entries=entries,
                    header_size=entries[0].data_offset,
                    magic=_magic(blob),
                )
                return probe
    return None


def _parse_entries(blob: bytes, start: int, entry_size: int, count: int) -> list[DirectoryEntry] | None:
    entries = []
    for i in range(count):
        base = start + i * entry_size
        table_id, record_size, record_count, data_offset = struct.unpack_from("<HHII", blob, base)
        if record_size == 0 or record_size > 65535 or table_id > 65535:
            return None
        entries.append(DirectoryEntry(table_id, record_size, record_count, data_offset))
    return entries


def _chains(entries: list[DirectoryEntry], directory_end: int, file_size: int) -> bool:
    expected = entries[0].data_offset
    if expected < directory_end or expected > 4096:
        return False
    for entry in entries:
        if entry.data_offset != expected:
            return False
        expected += entry.record_count * entry.record_size
    return expected == file_size


def _magic(blob: bytes) -> str | None:
    head = blob[:4]
    if len(head) == 4 and all(0x20 <= b < 0x7F for b in head):
        return head.decode("ascii")
    return None


def describe_header(blob: bytes, probe: DirectoryProbe) -> HeaderLayout:
    """Name the fixed header fields from their values. Unrecognised bytes stay as opaque spans."""
    fields: list[FieldLayout] = []

    def add(name: str, offset: int, length: int, type_: str, confidence: float, **extra: object) -> None:
        fields.append(
            FieldLayout(name=name, offset=offset, length=length, type=type_, confidence=confidence, method="structure", **extra)  # type: ignore[arg-type]
        )

    cursor = 0
    if probe.magic:
        add("magic", 0, 4, "char", 0.95, encoding="ascii", note=f"value {probe.magic!r}")
        cursor = 4
    table_count = len(probe.entries)
    names_left = ["version", "code_page", "table_count", "header_size"]
    # Little endian 16 bit values between the magic text and the directory.
    while cursor + 2 <= probe.directory_offset and names_left:
        (value,) = struct.unpack_from("<H", blob, cursor)
        if value == table_count and "table_count" in names_left:
            name = "table_count"
        elif value == probe.header_size and "header_size" in names_left:
            name = "header_size"
        elif value in CODE_PAGES and "code_page" in names_left:
            name = "code_page"
        elif "version" in names_left and value < 256:
            name = "version"
        else:
            break
        names_left.remove(name)
        add(name, cursor, 2, "uint16", 0.85, endianness="little", note=f"value {value}")
        cursor += 2
    if cursor < probe.directory_offset:
        add("reserved", cursor, probe.directory_offset - cursor, "bytes", 0.6)

    entry_fields = [
        ("table_id", 0, 2, "uint16"),
        ("record_size", 2, 2, "uint16"),
        ("record_count", 4, 4, "uint32"),
        ("data_offset", 8, 4, "uint32"),
    ]
    directory_fields = [
        FieldLayout(name=n, offset=o, length=length, type=t, endianness="little", confidence=0.95, method="structure")
        for n, o, length, t in entry_fields
    ]
    if probe.entry_size > 12:
        directory_fields.append(
            FieldLayout(name="reserved", offset=12, length=probe.entry_size - 12, type="bytes", confidence=0.6, method="structure")
        )
    return HeaderLayout(
        size=probe.header_size,
        fields=fields,
        directory_offset=probe.directory_offset,
        directory_entry_size=probe.entry_size,
        directory_fields=directory_fields,
    )
