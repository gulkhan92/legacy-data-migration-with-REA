"""The mapping decision for every source column. Pure functions: no database, no model, no network.

Inputs are the similarity of each column to every target field (from pgvector), the profile of the column, and the
approved mappings found in memory. Steps for each source table:

  1. Columns with a memory hit are settled: reuse, zero tokens.
  2. The target table is the one whose compatible fields fit the table's columns best (mean of the best similarity).
  3. Inside it, columns are matched to fields one to one, best pairs first, so a field is used by the column that fits it
     best (`inv_no` takes `source_key`, so `dtl_no` cannot). Pairs below the reject threshold are never made.
  4. A match is accepted automatically when its similarity and its margin over the best field that is still free are
     both high enough. Otherwise the column goes to the LLM step with its top candidates.
  5. A column left without a field is unmapped.

The type compatibility gate removes every incompatible field before ranking, whatever its similarity.
"""

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from app.mapping.compat import compatible
from app.mapping.thresholds import MappingThresholds
from app.profiling.models import ColumnProfile

MEMORY, AUTO, NEEDS_LLM, REVIEW, UNMAPPED = "memory", "auto", "needs_llm", "review", "unmapped"


@dataclass(frozen=True)
class TargetInfo:
    key: str  # "table.name"
    table: str
    name: str
    type: str


@dataclass
class MemoryHit:
    target_key: str
    distance: float


@dataclass
class ColumnInput:
    table: str
    profile: ColumnProfile
    sims: np.ndarray  # similarity to each target, same order as the targets list
    memory: MemoryHit | None = None

    @property
    def key(self) -> str:
        return f"{self.table}.{self.profile.name}"


@dataclass
class Decision:
    table: str
    column: str
    status: str
    target: str | None = None
    similarity: float | None = None
    margin: float | None = None
    confidence: float | None = None
    method: str = "embedding"
    candidates: list[tuple[str, float]] = field(default_factory=list)
    reason: str = ""
    target_table: str | None = None

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"


def confidence(similarity: float, margin: float, t: MappingThresholds) -> float:
    return round(t.similarity_weight * min(1.0, similarity) + (1 - t.similarity_weight) * min(1.0, max(0.0, margin) / t.margin_full), 3)


def decide(columns: list[ColumnInput], targets: list[TargetInfo], t: MappingThresholds) -> list[Decision]:
    by_key = {target.key: i for i, target in enumerate(targets)}
    tables: dict[str, list[ColumnInput]] = defaultdict(list)
    for column in columns:
        tables[column.table].append(column)
    target_tables = sorted({target.table for target in targets})
    decisions: dict[str, Decision] = {}

    for source_table, cols in tables.items():
        memory_cols = [c for c in cols if c.memory and c.memory.target_key in by_key]
        free_cols = [c for c in cols if c not in memory_cols]
        claimed: set[int] = set()
        votes: dict[str, int] = defaultdict(int)
        for c in memory_cols:
            assert c.memory is not None
            index = by_key[c.memory.target_key]
            claimed.add(index)
            votes[targets[index].table] += 1
            similarity = round(1.0 - c.memory.distance, 4)
            decisions[c.key] = Decision(c.table, c.profile.name, MEMORY, c.memory.target_key, similarity, None, round(similarity, 3), "memory",
                                        [(c.memory.target_key, similarity)], "Reused an approved mapping from memory", targets[index].table)  # fmt: skip

        # 2. the target table for this source table
        def best_in(c: ColumnInput, tt: str) -> float:
            values = [float(c.sims[i]) for i, tg in enumerate(targets) if tg.table == tt and compatible(c.profile, tg.type) and i not in claimed]
            return max(values) if values else 0.0

        scores = {tt: (votes[tt] + sum(best_in(c, tt) for c in free_cols)) / len(cols) for tt in target_tables}
        chosen_table = max(scores, key=lambda tt: scores[tt])
        if scores[chosen_table] < t.table_min_score and not memory_cols:
            for c in free_cols:
                decisions[c.key] = Decision(c.table, c.profile.name, UNMAPPED, reason=f"No target table fits {source_table}", target_table=None)
            continue

        # 3. one to one matching inside the chosen table, best pairs first
        pairs = [
            (float(c.sims[i]), ci, i)
            for ci, c in enumerate(free_cols)
            for i, tg in enumerate(targets)
            if tg.table == chosen_table and i not in claimed and compatible(c.profile, tg.type)
        ]
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        taken_cols: set[int] = set()
        taken_fields: set[int] = set(claimed)
        matched: dict[int, tuple[int, float]] = {}
        for sim, ci, fi in pairs:
            if sim < t.reject_similarity:
                break
            if ci in taken_cols or fi in taken_fields:
                continue
            matched[ci] = (fi, sim)
            taken_cols.add(ci)
            taken_fields.add(fi)
        still_free = [i for i, tg in enumerate(targets) if tg.table == chosen_table and i not in taken_fields]

        for ci, c in enumerate(free_cols):
            ranked = sorted(
                ((float(c.sims[i]), i) for i, tg in enumerate(targets) if tg.table == chosen_table and i not in claimed and compatible(c.profile, tg.type)),
                reverse=True,
            )
            top = [(targets[i].key, round(s, 4)) for s, i in ranked[: t.candidates]]
            if ci not in matched:
                reason = "No compatible field is similar enough" if not ranked or ranked[0][0] < t.reject_similarity else "The fields that fit are used by better matching columns"
                decisions[c.key] = Decision(c.table, c.profile.name, UNMAPPED, similarity=round(ranked[0][0], 4) if ranked else None, candidates=top,
                                            reason=reason, target_table=chosen_table)  # fmt: skip
                continue
            fi, sim = matched[ci]
            alternatives = [float(c.sims[i]) for i in still_free if compatible(c.profile, targets[i].type)]
            margin = sim - (max(alternatives) if alternatives else t.reject_similarity)
            if sim >= t.auto_similarity and margin >= t.auto_margin:
                status, reason = AUTO, f"Similarity {sim:.3f}, margin {margin:.3f}"
            else:
                status, reason = NEEDS_LLM, f"Similarity {sim:.3f} and margin {margin:.3f} are too low to accept without help"
            decisions[c.key] = Decision(c.table, c.profile.name, status, targets[fi].key, round(sim, 4), round(margin, 4), confidence(sim, margin, t),
                                        "embedding", top, reason, chosen_table)  # fmt: skip
    return [decisions[c.key] for c in columns]
