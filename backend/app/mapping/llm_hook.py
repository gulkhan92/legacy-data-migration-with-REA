"""The hand over to the LLM for columns the rules cannot settle (the `resolve_mappings` task of Phase 7).

Rules of the plan that are enforced here: one batched call per task (never one per column), about 12 columns per call and
every request under 5,000 tokens, metadata only (the same view an LLM may see of a column, which holds no PII values), and
the answer must pick one of the offered candidates or none. An answer that names anything else is discarded.
"""

import json
from collections.abc import Callable
from typing import Any

from app.mapping.decide import AUTO, NEEDS_LLM, REVIEW, UNMAPPED, Decision
from app.mapping.thresholds import MappingThresholds
from app.profiling.llm_view import llm_column_view
from app.profiling.models import ColumnProfile

BATCH_COLUMNS = 12
MAX_REQUEST_TOKENS = 5_000
Resolver = Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]
"""A resolver takes one request (a list of column payloads) and returns {column id: {"target": key or None,
"confidence": 0 to 1, "hint": optional transform hint}}. It is called once per request."""


def estimate_tokens(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"))) // 4


def build_payload(decision: Decision, profile: ColumnProfile, target_info: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": decision.key,
        "column": llm_column_view(profile),
        "candidates": [
            {"key": key, "type": target_info[key]["type"], "description": target_info[key]["description"], "similarity": similarity}
            for key, similarity in decision.candidates
        ],
    }


def build_requests(payloads: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split into requests of at most 12 columns and under 5,000 tokens each."""
    requests: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for payload in payloads:
        if current and (len(current) >= BATCH_COLUMNS or estimate_tokens([*current, payload]) > MAX_REQUEST_TOKENS):
            requests.append(current)
            current = []
        current.append(payload)
    if current:
        requests.append(current)
    return requests


def resolve(
    decisions: list[Decision],
    profiles: dict[str, ColumnProfile],
    target_info: dict[str, dict[str, Any]],
    resolver: Resolver,
    t: MappingThresholds,
) -> tuple[int, int]:
    """Ask the resolver about every NEEDS_LLM decision, in batches, and apply the answers. Returns (requests, tokens)."""
    pending = [d for d in decisions if d.status == NEEDS_LLM]
    if not pending:
        return 0, 0
    by_id = {d.key: d for d in pending}
    requests = build_requests([build_payload(d, profiles[d.key], target_info) for d in pending])
    tokens = 0
    for request in requests:
        tokens += estimate_tokens(request)
        answers = resolver(request)
        for item in request:
            decision = by_id[item["id"]]
            answer = answers.get(item["id"])
            offered = {c["key"] for c in item["candidates"]}
            if not answer or (answer.get("target") is not None and answer["target"] not in offered):
                decision.status, decision.reason = REVIEW, "The LLM answer was missing or named a field that was not offered"
                continue
            decision.method = "llm"
            decision.confidence = round(float(answer.get("confidence", 0.0)), 3)
            if answer["target"] is None:
                decision.status, decision.target, decision.reason = (UNMAPPED, None, "The LLM found no matching field") if decision.confidence >= t.llm_min_confidence else (REVIEW, None, "Unsure whether any field matches")
            elif decision.confidence >= t.llm_min_confidence:
                decision.status, decision.target, decision.reason = AUTO, answer["target"], "Chosen by the LLM among the candidates"
            else:
                decision.status, decision.target, decision.reason = REVIEW, answer["target"], "The LLM was not confident enough"
            if answer.get("hint"):
                decision.reason += f"; transform hint: {answer['hint']}"
    return len(requests), tokens
