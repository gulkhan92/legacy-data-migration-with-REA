"""Plug the router into the places that ask an LLM: mapping resolution and layout interpretation.

An adapter never raises: when the router cannot answer (budget, no provider, bad answers) it returns nothing, and the caller
falls back to human review. The reason is in the call log.
"""

import logging
from typing import Any

from app.discovery.cil_layout import ambiguity_key
from app.discovery.models import Ambiguity
from app.llm.errors import LlmError
from app.llm.router import LLMRouter, LlmContext
from app.mapping.llm_hook import Resolver

log = logging.getLogger("legacylift.llm")


def mapping_resolver(router: LLMRouter, ctx: LlmContext) -> Resolver:
    """The `resolve_mappings` task as a mapping resolver: one request per batch of at most 12 columns."""

    def resolve(request: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        try:
            result = router.complete_json("resolve_mappings", {"columns": request}, ctx)
        except LlmError as exc:
            log.warning("resolve_mappings unanswered: %s", exc.code)
            return {}
        return {a.id: {"target": a.target, "confidence": a.confidence, "hint": a.hint} for a in result.output.answers}  # type: ignore[attr-defined]

    return resolve


def layout_resolver(router: LLMRouter, ctx: LlmContext):  # type: ignore[no-untyped-def]
    """The `interpret_layout` task as a batch resolver: every open ambiguity of a file goes in one request.

    Returns {ambiguity key: {"type", "length", "endianness", "confidence"}}. Answers with low confidence are dropped, so a guess
    can never become a field."""

    def resolve(ambiguities: list[Ambiguity]) -> dict[str, dict[str, Any]]:
        if not ambiguities:
            return {}
        payload = {
            "ambiguities": [
                {"id": ambiguity_key(a), "kind": a.kind, "method": a.method, "detail": a.detail, "evidence": a.excerpt}
                for a in ambiguities
            ]
        }
        try:
            result = router.complete_json("interpret_layout", payload, ctx)
        except LlmError as exc:
            log.warning("interpret_layout unanswered: %s", exc.code)
            return {}
        return {
            a.id: {"type": a.type, "length": a.length, "endianness": a.endianness, "confidence": a.confidence}
            for a in result.output.answers  # type: ignore[attr-defined]
            if a.confidence >= 0.7 and a.type != "bytes" and a.length
        }

    return resolve
