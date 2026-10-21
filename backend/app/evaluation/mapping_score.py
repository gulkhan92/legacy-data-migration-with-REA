"""Score column mappings against the true mapping of the sample systems (ground truth).

The truth maps every source column to a target field ("table.field") or to None when the column has no counterpart.
A column counts as mapped correctly when the final decision names the true field, or is "unmapped" for a column that has
none. Columns still waiting for the LLM, or in review, are not counted as correct.
"""

from dataclasses import dataclass, field
from typing import Any

from app.mapping.decide import AUTO, MEMORY, NEEDS_LLM, REVIEW, UNMAPPED, Decision

SETTLED = {MEMORY, AUTO, UNMAPPED}


def commerce_truth(ground_truth: dict[str, Any]) -> dict[tuple[str, str], str | None]:
    truth: dict[tuple[str, str], str | None] = {}
    for legacy_table, spec in ground_truth["legacy_tables"].items():
        for column in spec["columns"]:
            target = column["target_field"]
            truth[(legacy_table, column["name"])] = f"{spec['target_table']}.{target}" if target else None
    return truth


def clinic_truth(ground_truth: dict[str, Any]) -> dict[tuple[str, str], str | None]:
    return {
        (table["name"], f["name"]): f.get("target")
        for table in ground_truth["layout"]["tables"]
        for f in table["fields"]
    }


@dataclass
class MappingScore:
    total: int = 0
    correct: int = 0
    wrong_settled: int = 0  # settled without help but wrong
    waiting: int = 0  # needs_llm or review
    reachable: int = 0  # waiting columns whose true answer is among the candidates (or that have none)
    wrong: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)
    by_method: dict[str, int] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0

    @property
    def ceiling(self) -> float:
        """What an LLM that always chose well among the candidates could reach."""
        return (self.correct + self.reachable) / self.total if self.total else 1.0


def score_decisions(decisions: list[Decision], truth: dict[tuple[str, str], str | None], *, tables: set[str] | None = None) -> MappingScore:
    score = MappingScore()
    for d in decisions:
        key = (d.table, d.column)
        if key not in truth or (tables is not None and d.table not in tables):
            continue
        expected = truth[key]
        score.total += 1
        score.by_method[d.status] = score.by_method.get(d.status, 0) + 1
        if d.status in SETTLED:
            if d.target == expected:
                score.correct += 1
            else:
                score.wrong_settled += 1
                score.wrong.append((d.key, expected, d.target))
        else:
            score.waiting += 1
            if expected is None or expected in {c[0] for c in d.candidates}:
                score.reachable += 1
            else:
                score.unreachable.append(d.key)
    return score
