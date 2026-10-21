"""Stream a legacy source into pandas chunks.

Every reader yields data frames of at most `chunk_size` rows whose index is the row identifier: the `rowid`
for SQLite tables, the zero based record index for binary files and the zero based data line for delimited
text. Nothing is executed: SQLite files are opened read only and immutable with the schema trust disabled,
binary files are decoded with the recovered layout, text files are parsed.
"""

import csv
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.discovery.models import LayoutSpec, RecordLayout

DEFAULT_CHUNK = 50_000


@dataclass
class ColumnHint:
    name: str
    declared_type: str | None = None
    layout_semantic: str | None = None
    native: str | None = None  # "int" for numbers read from binary fields, "text" for decoded text, None when unknown


@dataclass
class TableSource:
    name: str
    columns: list[ColumnHint]
    row_count: int | None
    chunks: Callable[[int], Iterator[pd.DataFrame]]
    entity_hint: str | None = None
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ SQLite


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SqliteReader:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro&immutable=1", uri=True)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")  # views and triggers of the file never run on behalf of the file
        return conn

    def tables(self) -> list[TableSource]:
        conn = self._connect()
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            out = []
            for name in names:
                info = conn.execute(f"PRAGMA table_info({_quote(name)})").fetchall()
                hints = [ColumnHint(c[1], c[2] or None) for c in info]
                count = conn.execute(f"SELECT count(*) FROM {_quote(name)}").fetchone()[0]
                out.append(TableSource(name, hints, count, self._chunker(name, [h.name for h in hints])))
            return out
        finally:
            conn.close()

    def _chunker(self, table: str, columns: list[str]) -> Callable[[int], Iterator[pd.DataFrame]]:
        def chunks(size: int) -> Iterator[pd.DataFrame]:
            conn = self._connect()
            try:
                select = ", ".join(_quote(c) for c in columns)
                try:
                    cursor = conn.execute(f"SELECT rowid, {select} FROM {_quote(table)} ORDER BY rowid")
                    with_rowid = True
                except sqlite3.OperationalError:  # WITHOUT ROWID table
                    cursor = conn.execute(f"SELECT {select} FROM {_quote(table)}")
                    with_rowid = False
                position = 0
                while rows := cursor.fetchmany(size):
                    if with_rowid:
                        index = [r[0] for r in rows]
                        data = [r[1:] for r in rows]
                    else:
                        index = list(range(position, position + len(rows)))
                        data = rows
                    position += len(rows)
                    yield pd.DataFrame.from_records(data, columns=columns, index=pd.Index(index, name="row"), coerce_float=False).astype(object)
            finally:
                conn.close()

        return chunks


# ------------------------------------------------------------------ binary records


NUMPY_CODES = {"int8": "i1", "uint8": "u1", "int16": "i2", "uint16": "u2", "int32": "i4", "uint32": "u4", "int64": "i8", "uint64": "u8"}


def record_dtype(table: RecordLayout) -> np.dtype:
    names, formats, offsets = [], [], []
    for f in table.fields:
        if f.type == "char" or f.type == "bytes":
            fmt = f"S{f.length}"
        else:
            order = ">" if f.endianness == "big" else "<"
            fmt = (order if f.length > 1 else "|") + NUMPY_CODES[f.type]
        names.append(f.name)
        formats.append(fmt)
        offsets.append(f.offset)
    return np.dtype({"names": names, "formats": formats, "offsets": offsets, "itemsize": table.record_size})


class BinaryReader:
    def __init__(self, path: Path, layout: LayoutSpec) -> None:
        self.path = path
        self.layout = layout

    def tables(self) -> list[TableSource]:
        out = []
        for table in self.layout.tables:
            if table.data_offset < 0 or table.record_count <= 0:
                continue
            hints = [
                ColumnHint(f.name, f.type, f.semantic, "text" if f.type in ("char", "bytes") else "int")
                for f in table.fields
            ]
            out.append(TableSource(table.name, hints, table.record_count, self._chunker(table), entity_hint=table.entity_hint))
        return out

    def _chunker(self, table: RecordLayout) -> Callable[[int], Iterator[pd.DataFrame]]:
        dtype = record_dtype(table)
        encodings = {f.name: (f.encoding or "cp1252") for f in table.fields}

        def chunks(size: int) -> Iterator[pd.DataFrame]:
            with self.path.open("rb") as handle:
                for start in range(0, table.record_count, size):
                    count = min(size, table.record_count - start)
                    handle.seek(table.data_offset + start * table.record_size)
                    block = np.fromfile(handle, dtype=dtype, count=count)
                    frame: dict[str, object] = {}
                    for f in table.fields:
                        column = block[f.name]
                        if f.type in ("char", "bytes"):
                            enc = encodings[f.name]
                            frame[f.name] = [b.decode(enc, errors="replace").rstrip(" \x00") for b in column]
                        else:
                            frame[f.name] = column.astype(np.int64)
                    yield pd.DataFrame(frame, index=pd.Index(range(start, start + count), name="row"))

        return chunks


# ------------------------------------------------------------------ delimited text


class CsvReader:
    def __init__(self, path: Path, name: str = "csv") -> None:
        self.path = path
        self.name = name

    def _encoding(self) -> str:
        sample = self.path.read_bytes()[:65536]
        try:
            sample.decode("utf-8-sig")
            return "utf-8-sig"
        except UnicodeDecodeError:
            return "cp1252"

    def _delimiter(self, encoding: str) -> str:
        with self.path.open("r", encoding=encoding, errors="replace", newline="") as handle:
            head = handle.read(8192)
        try:
            return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
        except csv.Error:
            return ","

    def tables(self) -> list[TableSource]:
        encoding = self._encoding()
        delimiter = self._delimiter(encoding)
        header = pd.read_csv(self.path, nrows=0, sep=delimiter, encoding=encoding, dtype=str)
        hints = [ColumnHint(str(c)) for c in header.columns]

        def chunks(size: int) -> Iterator[pd.DataFrame]:
            position = 0
            for frame in pd.read_csv(self.path, sep=delimiter, encoding=encoding, dtype=str, keep_default_na=False,
                                     chunksize=size, on_bad_lines="skip"):  # fmt: skip
                frame.index = pd.Index(range(position, position + len(frame)), name="row")
                position += len(frame)
                yield frame.astype(object)

        return [TableSource(self.name, hints, None, chunks, notes=[f"delimiter {delimiter!r}, encoding {encoding}"])]
