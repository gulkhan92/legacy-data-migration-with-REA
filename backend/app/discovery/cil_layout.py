"""Recover record layouts from the CIL that REA extracts from a .NET assembly.

REA command: `inspect-managed-members ASSEMBLY --json`. The result lists every method with its decoded
instruction anchors (calls, string literals, constants) in order, plus the resolved member references and
the user string literals. A legacy application that writes fixed records does so with a sequence of
`BinaryWriter.Write(...)` calls. Reading that sequence in order gives, for every record type:

  * the order and primitive type of each stored field (`Write(int)`, `Write(ushort)`, ...)
  * the width and padding of fixed width text (a helper call preceded by a width constant, writing bytes)
  * the byte order of numbers written through `BinaryPrimitives` or `IPAddress.HostToNetworkOrder`
  * the text encoding (the code page passed to `Encoding.GetEncoding`)
  * the name of the input column each field is read from (string literal before the dictionary lookup)

Declared field names (when the application keeps its record specification as data) come from the
constructor calls of a record specification type. Nothing is executed: only the decoded metadata is read.
Anything that cannot be resolved with confidence becomes an `Ambiguity` instead of a guess.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.discovery.models import Ambiguity, FieldLayout

SMALL_CONSTANTS = {f"ldc.i4.{n}": n for n in range(0, 9)} | {"ldc.i4.m1": -1}
WRITE_TYPES: dict[str, tuple[str, int]] = {
    "i1": ("int8", 1), "u1": ("uint8", 1), "i2": ("int16", 2), "u2": ("uint16", 2),
    "i4": ("int32", 4), "u4": ("uint32", 4), "i8": ("int64", 8), "u8": ("uint64", 8),
}  # fmt: skip
PRIMITIVES = {
    "Int16": ("int16", 2), "UInt16": ("uint16", 2), "Int32": ("int32", 4),
    "UInt32": ("uint32", 4), "Int64": ("int64", 8), "UInt64": ("uint64", 8),
}  # fmt: skip
TYPE_ALIASES = {"int": "int32", "short": "int16", "long": "int64", "byte": "uint8", "ushort": "uint16", "uint": "uint32"}
ENCODING_GETTERS = {"get_ASCII": "ascii", "get_UTF8": "utf-8", "get_Unicode": "utf-16-le", "get_Latin1": "latin-1"}
KNOWN_ENDIANNESS = {"little", "big"}
KNOWN_SEMANTICS_PATTERN = re.compile(r"^[a-z][a-z_]{1,24}$")
EXCERPT_TOKEN_CAP = 1500


@dataclass
class Call:
    """A resolved call target."""

    declaring: str | None
    name: str | None
    params: list[str]
    token: str
    is_definition: bool

    @property
    def label(self) -> str:
        return f"{self.declaring or '?'}::{self.name or self.token}"


@dataclass
class DeclaredField:
    name: str
    type: str | None = None
    size: int | None = None
    semantic: str | None = None
    encoding: str | None = None
    endianness: str | None = None
    note: str | None = None


@dataclass
class DerivedField:
    """One field as read from a writer method."""

    type: str  # a primitive type name, "char" for fixed text, or "bytes"
    length: int | None
    il_offset: int
    call: str
    endianness: str | None = None
    encoding: str | None = None
    padding: str | None = None
    source_hints: list[str] = field(default_factory=list)
    helper: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriterAnalysis:
    method: str  # "Namespace.Type.Method"
    method_token: str
    fields: list[DerivedField]
    ambiguities: list[Ambiguity]


class MemberIndex:
    def __init__(self, members: dict[str, Any]) -> None:
        self.methods = {m["token"]: m for m in members.get("methods", [])}
        self.method_list = members.get("methods", [])
        self.refs = {r["token"]: r for r in members.get("member_refs", [])}
        self.strings = {s["token"]: s["value"] for s in (members.get("user_strings") or {}).get("literals", [])}

    def resolve(self, anchor: dict[str, Any]) -> Call | None:
        if anchor.get("operand_kind") != "method":
            return None
        token = anchor["operand"]
        ref = self.refs.get(token)
        if ref:
            return Call(ref.get("parent_name"), ref.get("name"), ref["signature"].get("parameter_types", []), token, False)
        method = self.methods.get(token)
        if method:
            return Call(method.get("declaring_type"), method.get("name"), method["signature"].get("parameter_types", []), token, True)
        return None

    def anchors(self, method: dict[str, Any]) -> list[dict[str, Any]]:
        return (method.get("body") or {}).get("anchors", [])

    def string(self, anchor: dict[str, Any]) -> str | None:
        return self.strings.get(anchor["operand"]) if anchor.get("operand_kind") == "string" else None


def constant_value(anchor: dict[str, Any]) -> int | None:
    opcode = anchor.get("opcode", "")
    if opcode in SMALL_CONSTANTS:
        return SMALL_CONSTANTS[opcode]
    if anchor.get("operand_kind") == "constant":
        try:
            return int(anchor["operand"])
        except (TypeError, ValueError):
            return None
    return None


def is_binary_writer_write(call: Call | None) -> bool:
    return bool(call and call.declaring == "System.IO.BinaryWriter" and call.name == "Write")


# ------------------------------------------------------------------ helpers and encodings


def find_text_helpers(index: MemberIndex) -> dict[str, dict[str, Any]]:
    """Methods that turn text into fixed width bytes: they allocate an array, copy with Math.Min, and pad.

    Returns token -> {"padding": 0x20, "uses_encoding": ...}.
    """
    helpers: dict[str, dict[str, Any]] = {}
    for method in index.method_list:
        if not method.get("body"):
            continue
        calls = [c.label for c in (index.resolve(a) for a in index.anchors(method)) if c]
        opcodes = method["body"].get("opcode_counts", {})
        if "System.Array::Copy" in calls and "System.Math::Min" in calls and opcodes.get("newarr"):
            padding = next(
                (v for a in index.anchors(method) if (v := constant_value(a)) is not None and 0 <= v <= 255 and v not in (0, 1)), None
            )
            helpers[method["token"]] = {"padding": padding, "method": f"{method['declaring_type']}::{method['name']}"}
    return helpers


def find_encoding(index: MemberIndex) -> tuple[str | None, dict[str, Any]]:
    """The code page the application selects: `Encoding.GetEncoding(codepage, ...)` or a named encoding."""
    for method in index.method_list:
        anchors = index.anchors(method)
        for position, anchor in enumerate(anchors):
            call = index.resolve(anchor)
            if call and call.declaring == "System.Text.Encoding" and call.name == "GetEncoding" and call.params[:1] == ["i4"]:
                for earlier in reversed(anchors[:position]):
                    value = constant_value(earlier)
                    if value is not None and value > 3:
                        name = "windows-1252" if value == 1252 else f"cp{value}"
                        return name, {"method": f"{method['declaring_type']}::{method['name']}", "il_offset": earlier["il_offset"], "code_page": value}
    for method in index.method_list:
        for anchor in index.anchors(method):
            call = index.resolve(anchor)
            if call and call.declaring == "System.Text.Encoding" and call.name in ENCODING_GETTERS:
                return ENCODING_GETTERS[call.name], {"method": f"{method['declaring_type']}::{method['name']}", "il_offset": anchor["il_offset"]}
    return None, {}


# ------------------------------------------------------------------ writer analysis


def writer_candidates(index: MemberIndex) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Record writers (leaf writers called by another writer) and the top level writer, if any."""
    writing: dict[str, dict[str, Any]] = {}
    for method in index.method_list:
        writes = sum(1 for a in index.anchors(method) if is_binary_writer_write(index.resolve(a)))
        if writes >= 3:
            writing[method["token"]] = method
    called: set[str] = set()
    caller_of: dict[str, list[str]] = {}
    for token, method in writing.items():
        for anchor in index.anchors(method):
            if anchor.get("operand") in writing and anchor["operand"] != token and anchor.get("operand_kind") == "method":
                called.add(anchor["operand"])
                caller_of.setdefault(token, []).append(anchor["operand"])
    leaves = [m for t, m in writing.items() if t in called]
    top = next((m for t, m in writing.items() if t in caller_of), None)
    if not leaves and writing:
        leaves = [max(writing.values(), key=lambda m: len(index.anchors(m)))]
    # Keep the order in which the top level method calls them, which is the order of the file.
    if top:
        order = {a["operand"]: i for i, a in enumerate(index.anchors(top)) if a.get("operand") in {m["token"] for m in leaves}}
        leaves.sort(key=lambda m: order.get(m["token"], 10**9))
    return leaves, top


def analyse_writer(index: MemberIndex, method: dict[str, Any], helpers: dict[str, dict[str, Any]], encoding: str | None) -> WriterAnalysis:
    anchors = index.anchors(method)
    label = f"{method['declaring_type']}.{method['name']}"
    fields: list[DerivedField] = []
    ambiguities: list[Ambiguity] = []
    window: list[dict[str, Any]] = []

    for anchor in anchors:
        call = index.resolve(anchor)
        if not is_binary_writer_write(call):
            window.append(anchor)
            continue
        assert call is not None
        parameter = call.params[0] if call.params else ""
        hints = [s for a in window if (s := index.string(a)) is not None]
        window_calls = [(a, index.resolve(a)) for a in window]

        endianness: str | None = None
        for _, wc in window_calls:
            if wc and wc.declaring == "System.Net.IPAddress" and wc.name == "HostToNetworkOrder":
                endianness = "big"

        if parameter in WRITE_TYPES:
            type_name, length = WRITE_TYPES[parameter]
            fields.append(
                DerivedField(type_name, length, anchor["il_offset"], call.label, endianness=endianness or ("little" if length > 1 else None),
                             source_hints=hints, evidence={"window": _window_summary(index, window)})
            )
        elif parameter == "u1[]":
            primitive = next(
                ((PRIMITIVES[m.group(1)], m.group(2).lower().removesuffix("endian")) for _, wc in window_calls
                 if wc and wc.declaring == "System.Buffers.Binary.BinaryPrimitives"
                 and (m := re.fullmatch(r"Write(U?Int(?:16|32|64))(BigEndian|LittleEndian)", wc.name or ""))),
                None,
            )  # fmt: skip
            helper_index = next((i for i, (_, wc) in enumerate(window_calls) if wc and wc.token in helpers), None)
            if primitive:
                (type_name, length), order = primitive
                fields.append(DerivedField(type_name, length, anchor["il_offset"], call.label, endianness=order,
                                           source_hints=hints, helper="BinaryPrimitives", evidence={"window": _window_summary(index, window)}))  # fmt: skip
            elif helper_index is not None:
                helper_anchor, helper_call = window_calls[helper_index]
                assert helper_call is not None
                width = next((v for a in reversed(window[:helper_index]) if (v := constant_value(a)) is not None), None)
                info = helpers[helper_call.token]
                fields.append(
                    DerivedField(
                        "char", width, anchor["il_offset"], call.label, encoding=encoding,
                        padding=f"0x{info['padding']:02x}" if info.get("padding") is not None else None,
                        source_hints=hints, helper=info["method"],
                        evidence={"window": _window_summary(index, window), "width_known": width is not None},
                    )
                )  # fmt: skip
            else:
                fields.append(DerivedField("bytes", None, anchor["il_offset"], call.label, source_hints=hints, evidence={"window": _window_summary(index, window)}))
                ambiguities.append(
                    Ambiguity(kind="unknown_byte_write", method=label, il_offset=anchor["il_offset"],
                              detail="A byte array is written without a recognised width or number helper",
                              excerpt=_excerpt(index, label, window, anchor))  # fmt: skip
                )
        else:
            fields.append(DerivedField("bytes", None, anchor["il_offset"], call.label, source_hints=hints, evidence={"window": _window_summary(index, window)}))
            ambiguities.append(
                Ambiguity(kind="unsupported_write", method=label, il_offset=anchor["il_offset"],
                          detail=f"BinaryWriter.Write({parameter}) has no fixed size",
                          excerpt=_excerpt(index, label, window, anchor))  # fmt: skip
            )
        window = []
    return WriterAnalysis(label, method["token"], fields, ambiguities)


def _window_summary(index: MemberIndex, window: list[dict[str, Any]]) -> list[str]:
    out = []
    for a in window:
        call = index.resolve(a)
        text = index.string(a)
        if call:
            out.append(f"{a['opcode']} {call.label}({','.join(call.params)})")
        elif text is not None:
            out.append(f"ldstr {text!r}")
        elif constant_value(a) is not None:
            out.append(f"{a['opcode']} {constant_value(a)}")
    return out[-8:]


def _excerpt(index: MemberIndex, label: str, window: list[dict[str, Any]], write: dict[str, Any]) -> str:
    """A compact excerpt of the surrounding instructions, capped at about 1,500 tokens."""
    lines = [f"method {label}", *_window_summary(index, window), f"il {write['il_offset']}: BinaryWriter.Write"]
    text = "\n".join(lines)
    return text[: EXCERPT_TOKEN_CAP * 4]


# ------------------------------------------------------------------ declared field specification


def declared_tables(index: MemberIndex) -> list[dict[str, Any]]:
    """Record specifications the application keeps as data: ordered field declarations per table.

    Looks for a type initialiser that builds many objects from string literals and size constants, with
    a constructor shaped like (string, string, ..., size). Only structural facts are taken: name, type,
    size, semantic, encoding and endianness. Free text notes are kept as notes.
    """
    tables: list[dict[str, Any]] = []
    for method in index.method_list:
        if method.get("name") != ".cctor":
            continue
        anchors = index.anchors(method)
        if sum(1 for a in anchors if index.string(a) is not None) < 12:
            continue
        buffer: list[dict[str, Any]] = []
        header_strings: list[str] = []
        fields: list[DeclaredField] = []
        for anchor in anchors:
            opcode = anchor["opcode"]
            if opcode == "newarr":
                header_strings = [s for a in buffer if (s := index.string(a)) is not None]
                buffer = []
            elif opcode == "newobj":
                call = index.resolve(anchor)
                if call is None:
                    buffer = []
                    continue
                strings = [s for a in buffer if (s := index.string(a)) is not None]
                sizes = _sizes_after_type(index, buffer)
                string_params = sum(1 for p in call.params if p == "string")
                if call.is_definition and string_params >= 4 and strings:
                    fields.append(_declared_field(strings, sizes))
                elif call.is_definition and string_params >= 1 and "i4" in call.params and fields:
                    tables.append({"name": header_strings[0] if header_strings else None,
                                   "entity": header_strings[1] if len(header_strings) > 1 else None, "fields": fields})  # fmt: skip
                    fields, header_strings = [], []
                buffer = []
            elif opcode == "stelem.ref":
                buffer = []
            else:
                buffer.append(anchor)
    return tables


def _sizes_after_type(index: MemberIndex, buffer: list[dict[str, Any]]) -> list[int]:
    """Constants that follow the second string of a field declaration (the size argument).

    A constant before the name is the array index of the element being stored, not a size.
    """
    seen_strings = 0
    sizes: list[int] = []
    for anchor in buffer:
        if index.string(anchor) is not None:
            seen_strings += 1
        elif seen_strings >= 2 and (value := constant_value(anchor)) is not None:
            sizes.append(value)
    return sizes


def _declared_field(strings: list[str], sizes: list[int]) -> DeclaredField:
    name = strings[0]
    declared = DeclaredField(name=name, type=TYPE_ALIASES.get(strings[1], strings[1]) if len(strings) > 1 else None,
                             size=sizes[0] if sizes else None)  # fmt: skip
    for text in strings[2:]:
        if text in KNOWN_ENDIANNESS:
            declared.endianness = text
        elif text.startswith(("windows-", "utf-", "ascii", "iso-", "cp")):
            declared.encoding = text
        elif " " in text or "," in text:
            declared.note = text
        elif KNOWN_SEMANTICS_PATTERN.match(text) and declared.semantic is None:
            declared.semantic = text
    return declared


def match_declared(derived: list[DerivedField], tables: list[dict[str, Any]], used: set[int]) -> tuple[int, list[DeclaredField]] | None:
    """Find the declared table whose fields line up with a writer's fields (same count, types and sizes)."""
    best: tuple[float, int] | None = None
    for position, table in enumerate(tables):
        if position in used or len(table["fields"]) != len(derived):
            continue
        agree = sum(
            1 for d, spec in zip(derived, table["fields"], strict=True)
            if spec.type == d.type and (spec.size is None or d.length is None or spec.size == d.length)
        )  # fmt: skip
        score = agree / len(derived)
        if best is None or score > best[0]:
            best = (score, position)
    if best is None or best[0] < 0.9:
        return None
    return best[1], tables[best[1]]["fields"]


# ------------------------------------------------------------------ resolver hook


def ambiguity_key(ambiguity: Ambiguity) -> str:
    """Identifies one open question in a batch of questions sent to a resolver."""
    return f"{ambiguity.method}@{ambiguity.il_offset}"


BatchResolver = Callable[[list[Ambiguity]], dict[str, dict[str, Any]]]
"""Answers every open ambiguity of a file in one go: {ambiguity_key: {"type", "length", ...}}. This is the shape of the
`interpret_layout` LLM task, which takes one batched request per file."""

Resolver = Callable[[Ambiguity], dict[str, Any] | None]
"""Called with an ambiguity (and its capped evidence excerpt). Returns {"type", "length", ...} or None.

The LLM task `interpret_layout` (Phase 7) is plugged in here. Without a resolver an ambiguity is only
recorded, and the file based validation decides whether the layout is still trustworthy."""
