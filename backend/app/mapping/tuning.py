"""Tune the mapping thresholds on part of the fixtures and check them on the rest.

    uv run python -m app.mapping.tuning            # search, report, write config/mapping_thresholds.json
    uv run python -m app.mapping.tuning --dry-run  # search and report only

Split (by source table, so the held out tables are never seen while choosing):
  tuning set    all of the commerce system, plus the clinic tables PATIENT and ENCNTR
  held out set  the clinic tables COND, MEDS and OBS

The search maximises the columns mapped correctly without help and penalises settled columns that are wrong, which are
worse than columns sent to the LLM. It needs the embedding model and the REA runner (both fixtures are profiled for
real).
"""

import argparse
import itertools
import json

from app.evaluation.mapping_inputs import ScenarioInputs, scenario, truth_columns
from app.evaluation.mapping_score import MappingScore, score_decisions
from app.mapping.decide import decide
from app.mapping.thresholds import MappingThresholds, save_thresholds, thresholds_path

TUNING_TABLES = {
    "commerce": None,  # every table
    "clinic": {"PATIENT", "ENCNTR"},
}
HELD_OUT_TABLES = {"clinic": {"COND", "MEDS", "OBS"}}
WRONG_PENALTY = 2.0

GRID = {
    "reject_similarity": [0.56, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70],
    "auto_similarity": [0.62, 0.66, 0.70, 0.74, 0.78],
    "auto_margin": [0.0, 0.01, 0.02, 0.03, 0.05],
    "table_min_score": [0.55, 0.60, 0.65],
}


def evaluate(t: MappingThresholds, parts: dict[str, set[str] | None]) -> MappingScore:
    total = MappingScore()
    for name, tables in parts.items():
        s: ScenarioInputs = scenario(name)
        columns = [c for c in truth_columns(s) if tables is None or c.table in tables]
        decisions = decide(columns, s.targets, t)
        part = score_decisions(decisions, s.truth)
        total.total += part.total
        total.correct += part.correct
        total.wrong_settled += part.wrong_settled
        total.waiting += part.waiting
        total.reachable += part.reachable
        total.wrong += part.wrong
        total.unreachable += part.unreachable
    return total


def objective(score: MappingScore) -> float:
    return score.correct - WRONG_PENALTY * score.wrong_settled


def search(base: MappingThresholds | None = None) -> tuple[MappingThresholds, MappingScore, MappingScore]:
    base = base or MappingThresholds()
    best: tuple[tuple[float, float, float], MappingThresholds] | None = None
    names = list(GRID)
    for values in itertools.product(*GRID.values()):
        candidate = base.model_copy(update=dict(zip(names, values, strict=True)))
        if candidate.auto_similarity < candidate.reject_similarity:
            continue
        score = evaluate(candidate, TUNING_TABLES)
        # prefer the better objective, then fewer sent to the LLM, then the middle of the plateau (less extreme values)
        key = (objective(score), -score.waiting, -abs(candidate.reject_similarity - 0.62) - abs(candidate.auto_similarity - 0.70))
        if best is None or key > best[0]:
            best = (key, candidate)
    assert best is not None
    chosen = best[1]
    return chosen, evaluate(chosen, TUNING_TABLES), evaluate(chosen, HELD_OUT_TABLES)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    chosen, dev, held = search()
    chosen.tuning = {
        "tuning_set": {"commerce": "all tables", "clinic": sorted(TUNING_TABLES["clinic"] or [])},
        "held_out_set": {"clinic": sorted(HELD_OUT_TABLES["clinic"])},
        "objective": f"correct without help minus {WRONG_PENALTY} times wrong without help",
        "tuning": {"columns": dev.total, "correct": dev.correct, "wrong": dev.wrong_settled, "waiting": dev.waiting, "accuracy": round(dev.accuracy, 4)},
        "held_out": {"columns": held.total, "correct": held.correct, "wrong": held.wrong_settled, "waiting": held.waiting, "accuracy": round(held.accuracy, 4)},
    }
    print(json.dumps(chosen.model_dump(), indent=2))
    if not args.dry_run:
        print("written to", save_thresholds(chosen))
    else:
        print("dry run, would write", thresholds_path())


if __name__ == "__main__":
    main()
