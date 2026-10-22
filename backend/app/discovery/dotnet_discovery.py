"""Discovery of a proprietary binary data file through its .NET assembly.

REA commands used (see docs/architecture.md):
  inspect-managed-artifact ASSEMBLY --json     confirms a managed PE/CLI assembly and its deployment form
  inspect-managed-members  ASSEMBLY --json     members, signatures, CIL anchors and string literals

Pipeline:
  1. deterministic extraction of record layouts from the writer code (cil_layout)
  2. structural probe of the data file for its table directory (binary_structure)
  3. validation of the layout against the file, without trusting the analysis (layout_validation)
  4. when validation fails, or there is no usable analysis: statistical inference (statistical),
     marked as lower confidence

Ambiguities that the deterministic extractor cannot resolve are passed, as a compact evidence excerpt, to an
optional resolver (the `interpret_layout` LLM task of a later phase). Without one they are only recorded.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.discovery import cil_layout as cil
from app.discovery import statistical
from app.discovery.binary_structure import DirectoryProbe, describe_header, find_directory
from app.discovery.layout_validation import validate_layout
from app.discovery.models import Ambiguity, FieldLayout, LayoutSpec, RecordLayout, ValidationReport
from app.discovery.rea_adapter import ReaError, ReaRun, ReaRunner

CIL_BASE_CONFIDENCE = {"declared": 0.95, "derived": 0.85, "residual": 0.7}
UNVALIDATED_CAP = 0.8


@dataclass
class BinaryDiscovery:
    layout: LayoutSpec
    runs: list[ReaRun] = field(default_factory=list)
    # field evidence keyed by "TABLE.field": what the analysis saw (instruction offsets, calls, declared specs)
    field_evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    rejected_layout: LayoutSpec | None = None


def _safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "field"


def _table_name(writer_label: str) -> str:
    method = writer_label.rsplit(".", 1)[-1]
    return re.sub(r"^Write", "", method) or writer_label


def _build_tables(
    analyses: list[cil.WriterAnalysis],
    declared: list[dict[str, Any]],
    probe: DirectoryProbe | None,
    encoding: str | None,
    resolver: cil.Resolver | None,
    batch_resolver: cil.BatchResolver | None = None,
) -> tuple[list[RecordLayout], dict[str, dict[str, Any]], list[Ambiguity]]:
    """Turn writer analyses into record layouts, resolving unknown widths from the directory."""
    ambiguities = [a for analysis in analyses for a in analysis.ambiguities]
    batch_answers = batch_resolver(list(ambiguities)) if batch_resolver and ambiguities else {}

    # Pair writers with directory entries: equal record size first, then by order for the rest.
    pairing: dict[int, int] = {}
    if probe is not None:
        used: set[int] = set()
        for wi, analysis in enumerate(analyses):
            if all(f.length is not None for f in analysis.fields):
                size = sum(f.length or 0 for f in analysis.fields)
                match = next((ei for ei, e in enumerate(probe.entries) if ei not in used and e.record_size == size), None)
                if match is not None:
                    pairing[wi] = match
                    used.add(match)
        remaining = [ei for ei in range(len(probe.entries)) if ei not in used]
        for wi in (i for i in range(len(analyses)) if i not in pairing):
            if remaining:
                pairing[wi] = remaining.pop(0)

    tables: list[RecordLayout] = []
    evidence: dict[str, dict[str, Any]] = {}
    declared_used: set[int] = set()
    for wi, analysis in enumerate(analyses):
        entry = probe.entries[pairing[wi]] if probe is not None and wi in pairing else None
        fields = analysis.fields
        # 1. Ambiguities the code analysis left open go to the resolver (the interpret_layout task), when there is one.
        for amb in analysis.ambiguities:
            answer = resolver(amb) if resolver else batch_answers.get(cil.ambiguity_key(amb))
            target = next((f for f in fields if f.il_offset == amb.il_offset), None)
            if answer and target is not None:
                target.type = answer.get("type", target.type)
                target.length = answer.get("length", target.length)
                target.evidence["resolved_by"] = "resolver"
                ambiguities.remove(amb)
        # 2. A single field of unknown width takes what the record size leaves over.
        unknown = [f for f in fields if f.length is None]
        if unknown and entry is not None:
            known = sum(f.length or 0 for f in fields)
            if len(unknown) == 1 and entry.record_size > known:
                unknown[0].length = entry.record_size - known
                unknown[0].evidence["length_from"] = "record size minus the known fields"
        for f in fields:
            if f.length is None:
                ambiguities.append(
                    Ambiguity(kind="unknown_width", method=analysis.method, il_offset=f.il_offset,
                              detail="The width of a field could not be read from the code or the record size")  # fmt: skip
                )

        match = cil.match_declared(fields, declared, declared_used) if all(f.length is not None for f in fields) else None
        declared_fields: list[cil.DeclaredField] | None = None
        table_name = _table_name(analysis.method)
        entity_hint = None
        if match is not None:
            declared_used.add(match[0])
            declared_fields = match[1]
            spec = declared[match[0]]
            table_name = spec.get("name") or table_name
            entity_hint = spec.get("entity")

        offset = 0
        layout_fields: list[FieldLayout] = []
        for position, f in enumerate(fields):
            length = f.length or 0
            decl = declared_fields[position] if declared_fields else None
            if decl:
                name, name_source = decl.name, "declared"
            elif f.source_hints:
                name, name_source = _safe_name(f.source_hints[0]), "source_hint"
            else:
                name, name_source = f"field_{offset}", "generated"
            kind = "declared" if decl and f.length is not None else "residual" if f.evidence.get("length_from") else "derived"
            semantic = decl.semantic if decl else None
            layout_field = FieldLayout(
                name=name, offset=offset, length=length, type=f.type, encoding=f.encoding or (decl.encoding if decl else None),
                endianness=f.endianness, semantic=semantic, padding=f.padding,
                source_hint=f.source_hints[0] if f.source_hints else None, name_source=name_source,  # type: ignore[arg-type]
                confidence=CIL_BASE_CONFIDENCE[kind], method="cil", note=decl.note if decl else None,
            )  # fmt: skip
            layout_fields.append(layout_field)
            evidence[f"{table_name}.{name}"] = {
                "kind": "cil_write",
                "writer": analysis.method,
                "il_offset": f.il_offset,
                "call": f.call,
                "helper": f.helper,
                "source_hints": f.source_hints,
                "instructions": f.evidence.get("window"),
                "length_from": f.evidence.get("length_from"),
                "declared": ({"name": decl.name, "type": decl.type, "size": decl.size, "semantic": decl.semantic,
                              "encoding": decl.encoding, "endianness": decl.endianness} if decl else None),
                "declared_agrees": bool(decl and decl.type == f.type and decl.size in (None, f.length)),
                "encoding_found": encoding,
                "derived": {"type": f.type, "length": f.length, "endianness": f.endianness, "padding": f.padding},
            }  # fmt: skip
            offset += length
        tables.append(
            RecordLayout(
                name=table_name, entity_hint=entity_hint, table_id=entry.table_id if entry else None,
                record_size=entry.record_size if entry else offset, record_count=entry.record_count if entry else 0,
                data_offset=entry.data_offset if entry else -1, writer=analysis.method, fields=layout_fields,
                confidence=0.0,
            )  # fmt: skip
        )
    return tables, evidence, ambiguities


def discover_layout(
    runner: ReaRunner,
    assembly: Path | None,
    data_file: Path | None,
    *,
    resolver: cil.Resolver | None = None,
    batch_resolver: cil.BatchResolver | None = None,
) -> BinaryDiscovery:
    blob = data_file.read_bytes() if data_file else None
    probe = find_directory(blob) if blob else None
    result = BinaryDiscovery(layout=LayoutSpec(method="cil", confidence=0.0, validated=False))
    cil_tables: list[RecordLayout] = []
    evidence: dict[str, dict[str, Any]] = {}
    ambiguities: list[Ambiguity] = []

    if assembly is not None:
        try:
            artifact = runner.run("inspect-managed-artifact", assembly)
            result.runs.append(artifact)
            classification = artifact.normalized.get("classification", {})
            if classification.get("status") != "managed":
                result.notes.append(f"The assembly is not a plain managed assembly ({classification.get('status')})")
            else:
                members = runner.run("inspect-managed-members", assembly)
                result.runs.append(members)
                index = cil.MemberIndex(members.normalized)
                writers, _top = cil.writer_candidates(index)
                if not writers:
                    result.notes.append("No record writer code was found in the assembly")
                else:
                    encoding, enc_evidence = cil.find_encoding(index)
                    helpers = cil.find_text_helpers(index)
                    analyses = [cil.analyse_writer(index, w, helpers, encoding) for w in writers]
                    cil_tables, evidence, ambiguities = _build_tables(analyses, cil.declared_tables(index), probe, encoding, resolver, batch_resolver)
                    result.notes.append(f"Encoding {encoding}" if encoding else "No text encoding was found")
        except ReaError as exc:
            result.notes.append(f"REA could not analyse the assembly: {exc.kind}")

    cil_layout_spec: LayoutSpec | None = None
    if cil_tables:
        cil_layout_spec = _finish_cil_layout(blob, probe, cil_tables, ambiguities)

    if cil_layout_spec is not None and (cil_layout_spec.validated or blob is None):
        result.layout = cil_layout_spec
        result.field_evidence = _apply_confidence(cil_layout_spec, evidence)
        return result

    if blob is None:
        result.layout = cil_layout_spec or LayoutSpec(method="cil", confidence=0.0, validated=False,
                                                     limitations=["No usable analysis result and no data file"])  # fmt: skip
        return result

    # Fallback: statistical inference from the data file alone.
    fallback = statistical.infer_layout(blob, probe)
    if cil_layout_spec is not None:
        fallback.rejected_cil_layout = True
        fallback.validation = cil_layout_spec.validation
        fallback.limitations.append("The layout recovered from the code failed validation and was replaced")
        result.rejected_layout = cil_layout_spec
        result.field_evidence = {f"rejected:{k}": v for k, v in evidence.items()}
    fallback.ambiguities = ambiguities
    result.layout = fallback
    return result


def _finish_cil_layout(blob: bytes | None, probe: DirectoryProbe | None, tables: list[RecordLayout], ambiguities: list[Ambiguity]) -> LayoutSpec:
    header = describe_header(blob, probe) if blob is not None and probe is not None else None
    spec = LayoutSpec(
        format=probe.magic if probe else None, method="cil", confidence=0.0, validated=False,
        file_size=len(blob) if blob is not None else None, header=header, tables=tables, ambiguities=ambiguities,
    )  # fmt: skip
    if blob is None:
        spec.validation = ValidationReport(problems=["No data file was supplied, so the layout could not be checked"])
        spec.limitations.append("Not validated against a data file")
        return spec
    report, checks = validate_layout(blob, probe, tables)
    spec.validation = report
    spec.validated = report.passed
    for table in tables:
        for f in table.fields:
            check = checks.get((table.name, f.name))
            if check and check.checked and check.ratio < 1.0:
                f.confidence = round(f.confidence * check.ratio, 3)
    return spec


def _apply_confidence(spec: LayoutSpec, evidence: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    for table in spec.tables:
        for f in table.fields:
            if not spec.validated:
                f.confidence = min(f.confidence, UNVALIDATED_CAP)
        table.confidence = round(sum(f.confidence for f in table.fields) / max(1, len(table.fields)), 3)
    spec.confidence = round(sum(t.confidence for t in spec.tables) / max(1, len(spec.tables)), 3)
    return evidence
