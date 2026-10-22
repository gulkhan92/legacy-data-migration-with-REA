"""Helpers for the LLM tests: recorded provider responses, scripted transports, fake providers, a manual clock."""

import json
import random
import uuid
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from app.llm.errors import ProviderError
from app.llm.providers.base import Provider, ProviderRequest, ProviderResponse
from app.llm.resilience import RateInfo
from app.llm.router import LLMRouter, LlmContext, RouterConfig
from app.llm.store import MemoryStore, OrgLlmSettings

RECORDED = Path(__file__).parent / "recorded" / "llm"


def recorded(name: str) -> httpx.Response:
    data = json.loads((RECORDED / name).read_text(encoding="utf-8"))
    return httpx.Response(data["status"], headers=data["headers"], json=data["body"])


class ScriptedTransport(httpx.MockTransport):
    """Answers requests in order from a script of responses (or exceptions to raise). Keeps every request it saw."""

    def __init__(self, script: list[httpx.Response | Exception]) -> None:
        self.script = deque(script)
        self.requests: list[httpx.Request] = []
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def client_for(*script: httpx.Response | Exception) -> tuple[httpx.Client, ScriptedTransport]:
    transport = ScriptedTransport(list(script))
    return httpx.Client(transport=transport), transport


# ------------------------------------------------------------------ fake providers for router tests


class FakeProvider(Provider):
    """Plays a script of outcomes. An outcome is a ProviderResponse, a ProviderError to raise, or a callable(request)."""

    def __init__(self, name: str, script: list[Any] | None = None, *, model: str = "fake-model", configured: bool = True) -> None:
        super().__init__("key" if configured else "", model, client=httpx.Client())
        self.name = name
        self.script = deque(script or [])
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self._require_key()
        self.requests.append(request)
        item = self.script.popleft()
        if callable(item):
            item = item(request)
        if isinstance(item, ProviderError):
            raise item
        return item

    @property
    def calls(self) -> int:
        return len(self.requests)


def answer(payload: dict[str, Any], *, pick: int = 0, confidence: float = 0.9, tokens_in: int = 700, tokens_out: int = 120, cached: int = 0,
           rate: RateInfo | None = None, finish: str = "stop", model: str = "fake-model") -> ProviderResponse:  # fmt: skip
    """A good resolve_mappings answer: every column gets its `pick`-th candidate."""
    answers = [{"id": c["id"], "target": c["candidates"][pick]["key"], "confidence": confidence, "hint": None} for c in payload["columns"]]
    return ProviderResponse(json.dumps({"answers": answers}), tokens_in, tokens_out, cached, finish, model, rate, 120)


def text_response(text: str, *, tokens_in: int = 700, tokens_out: int = 60, finish: str = "stop") -> ProviderResponse:
    return ProviderResponse(text, tokens_in, tokens_out, 0, finish, "fake-model", None, 90)


def sample_payload(n: int = 2, *, offset: int = 0) -> dict[str, Any]:
    columns = []
    for i in range(offset, offset + n):
        columns.append(
            {
                "id": f"T.c{i}",
                "column": {"name": f"column number {i}", "type": "text", "semantic_type": "text", "pattern_class": "Aa+", "pii": False, "samples": ["Rock", "Jazz"]},
                "candidates": [
                    {"key": "x.a", "type": "text", "description": "First field of the target table.", "similarity": 0.66},
                    {"key": "x.b", "type": "text", "description": "Second field of the target table.", "similarity": 0.64},
                    {"key": "x.c", "type": "text", "description": "Third field of the target table.", "similarity": 0.61},
                ],
            }
        )
    return {"columns": columns}


class Clock:
    """A manual clock and a sleep that only moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_router(providers: dict[str, Provider], *, clock: Clock | None = None, **config: Any) -> tuple[LLMRouter, Clock]:
    clock = clock or Clock()
    cfg = RouterConfig(**config)
    return LLMRouter(providers, cfg, sleep=clock.sleep, clock=clock, rng=random.Random(7)), clock


def make_context(job: bool = True, *, job_budget: int = 6000, max_calls: int = 2, daily: int = 100_000, order: list[str] | None = None) -> tuple[LlmContext, MemoryStore]:
    job_id = uuid.uuid4() if job else None
    store = MemoryStore(settings=OrgLlmSettings(order or ["groq", "gemini"], job_budget, daily, max_calls))
    if job_id is not None:
        store.job_budgets[job_id] = job_budget
    return LlmContext(store, job_id), store
