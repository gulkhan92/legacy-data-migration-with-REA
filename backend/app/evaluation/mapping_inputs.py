"""Mapping inputs for the two sample systems, built with the real pipeline: profile, text, embedding, similarity.

Used by the tuning command and the tests. It needs the embedding model, and the REA runner for the layout of the clinic
file (discovery), exactly like a real run.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.discovery.dotnet_discovery import discover_layout
from app.discovery.models import LayoutSpec
from app.discovery.rea_adapter import REPO_ROOT, get_rea_runner
from app.evaluation.mapping_score import clinic_truth, commerce_truth
from app.mapping import texts
from app.mapping.decide import ColumnInput, TargetInfo
from app.mapping.embedding import get_embedding_service
from app.profiling.engine import profile_tables
from app.profiling.models import ProfileResult
from app.profiling.readers import BinaryReader, SqliteReader

FIXTURES = REPO_ROOT / "datasets" / "fixtures"
TARGETS = REPO_ROOT / "datasets" / "targets"


@dataclass
class ScenarioInputs:
    name: str
    profile: ProfileResult
    targets: list[TargetInfo]
    target_fields: list[dict[str, Any]]
    columns: list[ColumnInput]
    source_vectors: np.ndarray
    target_vectors: np.ndarray
    truth: dict[tuple[str, str], str | None]
    layout: LayoutSpec | None = None


def target_infos(fields: list[dict[str, Any]]) -> list[TargetInfo]:
    return [TargetInfo(f"{f['table']}.{f['name']}", f["table"], f["name"], f["type"]) for f in fields]


def build_columns(profile: ProfileResult, notes: dict[tuple[str, str], str | None], sims_for) -> list[ColumnInput]:  # type: ignore[no-untyped-def]
    columns = []
    for table in profile.tables:
        for p in table.columns:
            columns.append(ColumnInput(table.name, p, sims_for(table.name, p, notes.get((table.name, p.name)))))
    return columns


@lru_cache
def scenario(name: str) -> ScenarioInputs:
    service = get_embedding_service()
    fields = json.loads((TARGETS / f"{name}_target.json").read_text(encoding="utf-8"))["fields"]
    truth_file = json.loads((FIXTURES / name / "ground_truth.json").read_text(encoding="utf-8"))
    layout: LayoutSpec | None = None
    if name == "commerce":
        profile, _ = profile_tables(SqliteReader(FIXTURES / "commerce" / "commerce_legacy.sqlite").tables(), "sqlite", chunk_size=1000)
        truth = commerce_truth(truth_file)
        notes: dict[tuple[str, str], str | None] = {}
    else:
        layout = discover_layout(get_rea_runner(), FIXTURES / "clinic" / "ClinicDesk.dll", FIXTURES / "clinic" / "clinic.cdb").layout
        profile, _ = profile_tables(BinaryReader(FIXTURES / "clinic" / "clinic.cdb", layout).tables(), "dotnet_binary", chunk_size=5000)
        truth = clinic_truth(truth_file)
        notes = {(t.name, f.name): f.note for t in layout.tables for f in t.fields}

    target_vectors = service.encode([texts.target_text(f) for f in fields])
    pairs = [(t.name, p, notes.get((t.name, p.name))) for t in profile.tables for p in t.columns]
    source_vectors = service.encode([texts.QUERY_PREFIX + texts.source_text(table, p, note=note) for table, p, note in pairs])
    sims = source_vectors @ target_vectors.T
    columns = [ColumnInput(table, p, sims[i]) for i, (table, p, _) in enumerate(pairs)]
    return ScenarioInputs(name, profile, target_infos(fields), fields, columns, source_vectors, target_vectors, truth, layout)


def truth_columns(s: ScenarioInputs, tables: set[str] | None = None) -> list[ColumnInput]:
    """The columns that have a ground truth entry (system tables such as the trigger log do not)."""
    return [c for c in s.columns if (c.table, c.profile.name) in s.truth and (tables is None or c.table in tables)]


def describe_targets(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f"{f['table']}.{f['name']}": {"type": f["type"], "description": f["description"]} for f in fields}


__all__ = ["scenario", "truth_columns", "describe_targets", "ScenarioInputs", "Path"]
