"""Optional live check against the real providers. Skipped unless RUN_LIVE_LLM=1 and a key is set.

    RUN_LIVE_LLM=1 GROQ_API_KEY=... GEMINI_API_KEY=... uv run pytest tests/test_llm_live.py -s

It sends one tiny synthetic request (no real data) per configured provider through the router and checks the whole path: the
request is accepted, the strict schema is honoured and the usage figures are read. CI never runs it.
"""

import os

import pytest

from app.core.config import get_settings
from app.llm.factory import build_router
from app.llm.router import LlmContext
from app.llm.store import MemoryStore
from tests.llm_helpers import sample_payload

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1", reason="set RUN_LIVE_LLM=1 and provider keys to run live checks")


@pytest.mark.parametrize("name", ["groq", "gemini"])
def test_live_provider_answers_with_valid_json(name: str) -> None:
    router = build_router(get_settings())
    provider = router.providers[name]
    if not provider.configured:
        pytest.skip(f"no key for {name}")
    store = MemoryStore()
    store.settings.provider_order = [name]
    payload = sample_payload(2)
    result = router.complete_json("resolve_mappings", payload, LlmContext(store, None))
    assert result.provider == name and result.tokens > 0 and {a.id for a in result.output.answers} == {"T.c0", "T.c1"}  # type: ignore[attr-defined]
    print(name, "tokens", result.tokens_in, result.tokens_out, "cached", result.cached_tokens, "limits", router.status()[name])


def test_live_connection_test_runs_for_every_configured_provider() -> None:
    router = build_router(get_settings())
    for name, provider in router.providers.items():
        if provider.configured:
            outcome = router.test_connection(name, LlmContext(MemoryStore(), None))
            assert outcome["ok"], outcome
