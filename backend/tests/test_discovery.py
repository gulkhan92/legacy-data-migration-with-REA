"""Phase 4: REA integration and discovery on both sample systems.

These tests run the real REA runner (Node.js and `npm ci` in rea-runner/). They are skipped with a clear
message when the runner is not installed.
"""

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.discovery import cil_layout as cil
from app.discovery import dotnet_discovery, statistical
from app.discovery.binary_structure import find_directory
from app.discovery.dotnet_discovery import discover_layout
from app.discovery.layout_validation import validate_layout
from app.discovery.models import Ambiguity
from app.discovery.rea_adapter import ReaError, ReaRunner, get_rea_runner
from app.discovery.sqlite_discovery import discover_sqlite
from app.evaluation.discovery_score import score_layout, score_sqlite

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "datasets" / "fixtures"
COMMERCE = FIXTURES / "commerce" / "commerce_legacy.sqlite"
DLL = FIXTURES / "clinic" / "ClinicDesk.dll"
CDB = FIXTURES / "clinic" / "clinic.cdb"

REA_INSTALLED = (ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
needs_rea = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner (Node.js 24.15 or newer)")


@pytest.fixture(scope="session")
def commerce_discovery():  # type: ignore[no-untyped-def]
    return discover_sqlite(get_rea_runner(), COMMERCE)


@pytest.fixture(scope="session")
def clinic_discovery():  # type: ignore[no-untyped-def]
    return discover_layout(get_rea_runner(), DLL, CDB)


def truth(name: str) -> dict:
    import json

    return json.loads((FIXTURES / name / "ground_truth.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the adapter


@needs_rea
def test_runner_reports_the_pinned_rea_version(commerce_discovery) -> None:  # type: ignore[no-untyped-def]
    _, runs = commerce_discovery
    assert {r.rea_version for r in runs} == {"6.7.0"}
    assert all(r.duration_ms >= 0 and r.wall_ms >= r.duration_ms for r in runs)


@needs_rea
@pytest.mark.parametrize(
    "command,path,kind",
    [
        ("rm", str(COMMERCE), "unknown_command"),
        ("capture-process", str(COMMERCE), "unknown_command"),  # runtime capture is not exposed
        ("inspect-sqlite-database", str(COMMERCE.parent / "missing.sqlite"), "file_not_found"),
        ("inspect-sqlite-database", str(DLL), "analysis_failed"),  # not a database
        ("inspect-managed-artifact", str(COMMERCE), "analysis_failed"),  # not a managed assembly
    ],
)
def test_structured_errors(command: str, path: str, kind: str) -> None:
    runner = get_rea_runner()
    with pytest.raises(ReaError) as info:
        runner.run(command, Path(path))
    assert info.value.kind == kind
    assert info.value.command == command
    assert str(ROOT) not in info.value.message  # host paths are not echoed back


@needs_rea
def test_rea_error_codes_are_exposed() -> None:
    with pytest.raises(ReaError) as info:
        get_rea_runner().run("inspect-sqlite-database", DLL)
    assert info.value.code == "invalid_request"


@needs_rea
def test_options_outside_the_whitelist_are_refused() -> None:
    with pytest.raises(ReaError) as info:
        get_rea_runner().run("inspect-sqlite-database", COMMERCE, ["--query", "DROP TABLE x"])
    assert info.value.kind == "invalid_arguments"
    with pytest.raises(ReaError) as info:
        get_rea_runner().run("inspect-sqlite-database", COMMERCE, ["--row-limit", "-1"])
    assert info.value.kind == "invalid_arguments"


@needs_rea
def test_runner_refuses_paths_outside_the_allowed_roots(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("REA_ALLOWED_ROOTS", str(ROOT / "backend"))
    # the adapter passes through only a fixed set of variables, so run the wrapper directly here
    completed = subprocess.run(
        ["node", str(ROOT / "rea-runner" / "wrapper.mjs"), "inspect-sqlite-database", str(COMMERCE)],
        capture_output=True, text=True, env={**__import__("os").environ, "REA_ALLOWED_ROOTS": str(ROOT / "backend")},
    )  # fmt: skip
    assert '"path_not_allowed"' in completed.stdout and completed.returncode == 1


def test_timeout_is_reported() -> None:
    runner = ReaRunner([sys.executable, "-c", "import time; time.sleep(30)", "--"], timeout_seconds=1, grace_seconds=0)
    with pytest.raises(ReaError) as info:
        runner.run("inspect-sqlite-database", COMMERCE)
    assert info.value.kind == "timeout"


def test_missing_runner_and_garbage_output_are_reported() -> None:
    with pytest.raises(ReaError) as info:
        ReaRunner(["definitely-not-a-real-binary-xyz"]).run("inspect-sqlite-database", COMMERCE)
    assert info.value.kind == "runner_unavailable"
    with pytest.raises(ReaError) as info:
        ReaRunner([sys.executable, "-c", "print('not json')", "--"]).run("inspect-sqlite-database", COMMERCE)
    assert info.value.kind == "runner_failed"


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker is not available")
def test_container_mode_runs_with_no_network_and_a_read_only_mount() -> None:
    image = "legacylift-rea-runner:6.7.0"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        pytest.skip(f"image {image} is not built (docker build -t {image} rea-runner)")
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only", "--user", "10001:10001", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp:size=64m,noexec,nosuid", "-v", "{workdir}:/work:ro", image,
    ]  # fmt: skip
    runner = ReaRunner(command, timeout_seconds=120, container_workdir="/work")
    schema, runs = discover_sqlite(runner, COMMERCE, sample_rows=0)
    assert {t.name for t in schema.tables} >= {"CUST_MST", "INV_HDR"} and runs[0].rea_version == "6.7.0"
    layout = discover_layout(runner, DLL, CDB).layout
    assert layout.validated and layout.method == "cil"


# ------------------------------------------------------------------ SQLite discovery


@needs_rea
def test_sqlite_discovery_recovers_at_least_95_percent(commerce_discovery) -> None:  # type: ignore[no-untyped-def]
    schema, _ = commerce_discovery
    score = score_sqlite(schema, truth("commerce"))
    assert score.recall >= 0.95, score.missing
    assert score.recall == 1.0, score.missing  # the fixture is fully recoverable


@needs_rea
def test_sqlite_discovery_converts_samples_and_objects(commerce_discovery) -> None:  # type: ignore[no-untyped-def]
    schema, runs = commerce_discovery
    assert len(runs) == 1 + 5  # the schema, then one row sample for each of the five ordinary tables
    assert schema.engine.startswith("SQLite") and schema.database_encoding == "UTF-8"
    cust = next(t for t in schema.tables if t.name == "CUST_MST")
    assert len(cust.sample_rows) == 5 and cust.sample_truncated
    assert cust.sample_rows[0]["cust_no"] == 1 and isinstance(cust.sample_rows[0]["f_nm"], str)
    assert {i.name for i in schema.indexes} >= {"IX_INV_CUST", "IX_DTL_INV", "IX_CUST_NM"}
    assert [v.name for v in schema.views] == ["V_CUST_LIST"] and [t.name for t in schema.triggers] == ["TRG_INV_LOG"]
    assert all(c.declared_type for t in schema.tables for c in t.columns)


# ------------------------------------------------------------------ .NET discovery


@needs_rea
def test_dotnet_discovery_recovers_at_least_95_percent(clinic_discovery) -> None:  # type: ignore[no-untyped-def]
    score = score_layout(clinic_discovery.layout, truth("clinic")["layout"], by="name")
    assert score.recall >= 0.95, score.missing
    assert score.recall == 1.0, score.missing


@needs_rea
def test_dotnet_layout_is_validated_and_confident(clinic_discovery) -> None:  # type: ignore[no-untyped-def]
    layout = clinic_discovery.layout
    assert layout.method == "cil" and layout.validated and layout.confidence >= 0.9
    assert layout.format == "CDB1" and not layout.rejected_cil_layout
    report = layout.validation
    assert report.size_rule_ok and report.tables_matched and report.passed and not report.problems
    assert report.computed_size == report.file_size == CDB.stat().st_size
    assert report.records_checked > 1000
    assert layout.ambiguities == []


@needs_rea
def test_dotnet_details_match_the_true_layout(clinic_discovery) -> None:  # type: ignore[no-untyped-def]
    layout, true_layout = clinic_discovery.layout, truth("clinic")["layout"]
    assert [t.record_size for t in layout.tables] == [t["record_size"] for t in true_layout["tables"]]
    assert [t.record_count for t in layout.tables] == [t["record_count"] for t in true_layout["tables"]]
    assert [t.data_offset for t in layout.tables] == [t["data_offset"] for t in true_layout["tables"]]
    for found, true in zip(layout.tables, true_layout["tables"], strict=True):
        for f, t in zip(found.fields, true["fields"], strict=True):
            assert (f.name, f.offset, f.length, f.type) == (t["name"], t["offset"], t["length"], t["type"])
            assert f.endianness == t.get("endianness")  # little, big, or none for single bytes and text
            assert f.name_source == "declared"
            if t["type"] == "char":
                assert f.encoding == "windows-1252" and f.padding == "0x20"
    big = [(t.name, f.name) for t in layout.tables for f in t.fields if f.endianness == "big"]
    assert big == [("ENCNTR", "CostCents")]
    header = layout.header
    assert header.size == 96 and [f.name for f in header.fields[:5]] == ["magic", "version", "code_page", "table_count", "header_size"]
    assert header.directory_offset == 16 and header.directory_entry_size == 16


@needs_rea
def test_every_field_has_evidence(clinic_discovery) -> None:  # type: ignore[no-untyped-def]
    evidence = clinic_discovery.field_evidence
    layout = clinic_discovery.layout
    expected = {f"{t.name}.{f.name}" for t in layout.tables for f in t.fields}
    assert set(evidence) == expected and len(expected) == 44
    sample = evidence["PATIENT.LastNm"]
    assert sample["kind"] == "cil_write" and sample["writer"].endswith("WritePatient")
    assert sample["il_offset"] > 0 and sample["call"] == "System.IO.BinaryWriter::Write"
    assert sample["helper"].endswith("Legacy::Fixed") and sample["source_hints"] == ["LAST"]
    assert sample["derived"]["length"] == 30 and sample["declared_agrees"] is True
    assert any("ldc.i4.s 30" in line for line in sample["instructions"])
    assert evidence["ENCNTR.CostCents"]["helper"] == "BinaryPrimitives"
    assert evidence["ENCNTR.CostCents"]["derived"]["endianness"] == "big"


@needs_rea
def test_declared_names_do_not_leak_the_target_schema(clinic_discovery) -> None:  # type: ignore[no-untyped-def]
    text = DLL.read_bytes()
    for target in ("patients.last_name", "encounters.start_time", "total_cost_cents"):
        assert target.encode("utf-16-le") not in text  # the legacy application knows nothing about the target schema
    names = {f.name for t in clinic_discovery.layout.tables for f in t.fields}
    assert "last_name" not in names and "LastNm" in names


@needs_rea
def test_failed_validation_falls_back_to_statistical_inference(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An analysis result that disagrees with the file is rejected, not trusted."""
    original = cil.analyse_writer

    def corrupt(index, method, helpers, encoding):  # type: ignore[no-untyped-def]
        analysis = original(index, method, helpers, encoding)
        if method["name"] == "WritePatient":
            analysis.fields[1].length = 28  # LastNm is really 30 bytes wide
        return analysis

    monkeypatch.setattr(cil, "analyse_writer", corrupt)
    result = discover_layout(get_rea_runner(), DLL, CDB)
    layout = result.layout
    assert layout.method == "statistical" and layout.rejected_cil_layout and not layout.validated
    assert layout.confidence <= statistical.MAX_CONFIDENCE
    assert any("PATIENT" in p or "tables" in p.lower() for p in layout.validation.problems)
    assert result.rejected_layout is not None and result.rejected_layout.method == "cil"
    # the statistical layout still follows the directory and finds most of the true boundaries
    score = score_layout(layout, truth("clinic")["layout"], by="position")
    assert score.recall >= 0.6, (score.recall, score.missing[:10])
    assert all(f.confidence <= statistical.MAX_CONFIDENCE for t in layout.tables for f in t.fields)


@needs_rea
def test_data_file_alone_uses_statistical_inference() -> None:
    result = discover_layout(get_rea_runner(), None, CDB)
    layout = result.layout
    assert layout.method == "statistical" and layout.confidence <= statistical.MAX_CONFIDENCE
    assert layout.format == "CDB1" and len(layout.tables) == 5
    assert [t.record_size for t in layout.tables] == [177, 117, 160, 134, 146]
    assert all(f.name_source == "generated" for t in layout.tables for f in t.fields)
    score = score_layout(layout, truth("clinic")["layout"], by="position")
    assert score.recall >= 0.6, (score.recall, score.missing[:10])


@needs_rea
def test_assembly_alone_gives_an_unvalidated_layout() -> None:
    result = discover_layout(get_rea_runner(), DLL, None)
    layout = result.layout
    assert layout.method == "cil" and not layout.validated and layout.confidence <= dotnet_discovery.UNVALIDATED_CAP
    assert score_layout(layout, truth("clinic")["layout"], by="name").recall >= 0.95


@needs_rea
def test_wrong_kind_of_file_is_reported_not_guessed() -> None:
    result = discover_layout(get_rea_runner(), COMMERCE, None)  # a database is not an assembly
    assert result.layout.confidence == 0.0 and not result.layout.tables
    assert any("could not analyse" in n for n in result.notes)


# ------------------------------------------------------------------ file structure and validation (no REA needed)


def test_directory_probe_finds_the_real_directory() -> None:
    blob = CDB.read_bytes()
    probe = find_directory(blob)
    assert probe and probe.directory_offset == 16 and probe.entry_size == 16 and probe.header_size == 96
    assert [e.record_size for e in probe.entries] == [177, 117, 160, 134, 146]


def test_directory_probe_rejects_random_data_and_truncated_files() -> None:
    import random

    rnd = random.Random(1)
    assert find_directory(bytes(rnd.randrange(256) for _ in range(5000))) is None
    assert find_directory(CDB.read_bytes()[:-1]) is None  # the size rule no longer holds
    assert find_directory(b"") is None


def test_record_size_is_inferred_from_a_single_table_file_without_a_directory() -> None:
    blob = CDB.read_bytes()
    patients_only = blob[96 : 96 + 177 * 306]  # no header, no directory
    assert statistical.infer_record_size(patients_only) == (0, 177)
    layout = statistical.infer_layout(patients_only)
    assert layout.method == "statistical" and layout.header is None and layout.tables[0].record_size == 177
    assert any("record size was inferred" in note for note in layout.limitations)
    assert statistical.infer_layout(b"").confidence == 0.0


def test_validation_rejects_layouts_that_do_not_fit_the_file() -> None:
    from app.discovery.models import FieldLayout, RecordLayout

    blob = CDB.read_bytes()
    probe = find_directory(blob)
    tables = [
        RecordLayout(name=f"T{e.table_id}", record_size=e.record_size, record_count=e.record_count, data_offset=e.data_offset,
                     fields=[FieldLayout(name="all", offset=0, length=e.record_size, type="char", encoding="cp1252", confidence=0.5)])
        for e in probe.entries
    ]  # fmt: skip
    report, _ = validate_layout(blob, probe, tables)
    assert report.size_rule_ok and report.tables_matched
    assert not report.passed  # binary data is not printable text
    report, _ = validate_layout(blob[:-10], None, tables)
    assert not report.passed and "directory" in report.problems[0]


# ------------------------------------------------------------------ ambiguity handling (synthetic CIL)


def _members(write_anchors: list[dict]) -> dict:
    write_ref = {"token": "0x0a000001", "parent_name": "System.IO.BinaryWriter", "name": "Write", "signature": {"parameter_types": ["u1[]"]}}
    int_ref = {"token": "0x0a000002", "parent_name": "System.IO.BinaryWriter", "name": "Write", "signature": {"parameter_types": ["i4"]}}
    helper_ref = {"token": "0x0a000003", "parent_name": "Vendor.Codec", "name": "Pack", "signature": {"parameter_types": ["string"]}}
    anchors = [
        {"il_offset": 1, "opcode": "callvirt", "operand_kind": "method", "operand": "0x0a000002"},
        {"il_offset": 10, "opcode": "ldstr", "operand_kind": "string", "operand": "0x70000001"},
        {"il_offset": 15, "opcode": "call", "operand_kind": "method", "operand": "0x0a000003"},
        {"il_offset": 20, "opcode": "callvirt", "operand_kind": "method", "operand": "0x0a000001"},
        {"il_offset": 30, "opcode": "callvirt", "operand_kind": "method", "operand": "0x0a000002"},
        {"il_offset": 40, "opcode": "callvirt", "operand_kind": "method", "operand": "0x0a000002"},
        *write_anchors,
    ]
    return {
        "methods": [{"token": "0x06000001", "declaring_type": "Vendor.Store", "name": "WriteRow", "signature": {"parameter_types": []},
                     "body": {"anchors": anchors, "opcode_counts": {}}}],
        "member_refs": [write_ref, int_ref, helper_ref],
        "user_strings": {"literals": [{"token": "0x70000001", "value": "NAME"}]},
    }  # fmt: skip


def test_unknown_byte_writes_become_ambiguities_with_a_capped_excerpt() -> None:
    index = cil.MemberIndex(_members([]))
    analysis = cil.analyse_writer(index, index.method_list[0], {}, "windows-1252")
    assert [a.kind for a in analysis.ambiguities] == ["unknown_byte_write"]
    ambiguity = analysis.ambiguities[0]
    assert "ldstr 'NAME'" in ambiguity.excerpt and "Vendor.Codec::Pack" in ambiguity.excerpt
    assert len(ambiguity.excerpt) <= cil.EXCERPT_TOKEN_CAP * 4
    assert [f.type for f in analysis.fields] == ["int32", "bytes", "int32", "int32"]


def test_the_resolver_hook_receives_ambiguities_and_can_resolve_them() -> None:
    index = cil.MemberIndex(_members([]))
    analysis = cil.analyse_writer(index, index.method_list[0], {}, "windows-1252")
    seen: list[Ambiguity] = []

    def resolver(amb: Ambiguity) -> dict | None:
        seen.append(amb)
        return {"type": "char", "length": 12}

    tables, _, ambiguities = dotnet_discovery._build_tables([analysis], [], None, "windows-1252", resolver)
    assert len(seen) == 1 and seen[0].excerpt
    assert [f.length for f in tables[0].fields] == [4, 12, 4, 4]
    assert tables[0].fields[1].type == "char"


def test_a_single_unknown_width_is_resolved_from_the_record_size() -> None:
    from app.discovery.binary_structure import DirectoryEntry, DirectoryProbe

    index = cil.MemberIndex(_members([]))
    analysis = cil.analyse_writer(index, index.method_list[0], {}, None)
    probe = DirectoryProbe(16, 16, [DirectoryEntry(1, 28, 3, 32)], 32)
    tables, _, _ = dotnet_discovery._build_tables([analysis], [], probe, None, None)
    assert [f.length for f in tables[0].fields] == [4, 16, 4, 4] and tables[0].fields[1].confidence < 0.85
    assert copy.deepcopy(tables[0]).record_size == 28


def test_large_command_output_is_not_truncated() -> None:
    """The members listing is about 1 MB; it must arrive complete through a pipe."""
    if not REA_INSTALLED:
        pytest.skip("REA runner not installed")
    run = get_rea_runner().run("inspect-managed-members", DLL)
    assert len(run.normalized["methods"]) > 100 and len(str(run.result)) > 500_000
