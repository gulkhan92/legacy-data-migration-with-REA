"""Mapping thresholds. They live in a JSON file, written by the tuning command, not in code.

    reject_similarity     below this a column has no target (unmapped)
    auto_similarity       a match needs at least this similarity to be accepted without help
    auto_margin           and must beat the best still free alternative by at least this much
    margin_full           the margin at which the margin part of the confidence is complete
    memory_distance       cosine distance under which an approved mapping from memory is reused
    table_min_score       mean best similarity a target table needs for a source table to map to it at all
    llm_min_confidence    an LLM answer below this goes to human review
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.core.config import get_settings

DEFAULT_FILE = Path(__file__).resolve().parents[2] / "config" / "mapping_thresholds.json"


class MappingThresholds(BaseModel):
    reject_similarity: float = 0.62
    auto_similarity: float = 0.70
    auto_margin: float = 0.0
    margin_full: float = 0.15
    memory_distance: float = 0.06
    table_min_score: float = 0.60
    llm_min_confidence: float = 0.70
    candidates: int = 3
    similarity_weight: float = 0.5  # share of the confidence that comes from the similarity, the rest from the margin
    tuning: dict = {}  # how the values were chosen: data split, scores on both parts


def thresholds_path() -> Path:
    configured = get_settings().mapping_thresholds_file
    return Path(configured) if configured else DEFAULT_FILE


@lru_cache
def _load(path: str) -> MappingThresholds:
    file = Path(path)
    if file.exists():
        return MappingThresholds.model_validate(json.loads(file.read_text(encoding="utf-8")))
    return MappingThresholds()


def load_thresholds() -> MappingThresholds:
    return _load(str(thresholds_path()))


def save_thresholds(values: MappingThresholds, path: Path | None = None) -> Path:
    target = path or thresholds_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(values.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _load.cache_clear()
    return target
