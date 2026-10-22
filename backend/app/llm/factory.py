"""Build the process wide router from the settings. Breakers and rate limit buckets live in it, so they must be shared."""

import threading
import uuid

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.llm.providers.base import Provider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.groq import GroqProvider
from app.llm.router import LLMRouter, LlmContext, RouterConfig
from app.llm.store import DbStore

_router: LLMRouter | None = None
_lock = threading.Lock()


def build_router(settings: Settings | None = None, *, client: httpx.Client | None = None) -> LLMRouter:
    s = settings or get_settings()
    providers: dict[str, Provider] = {
        "groq": GroqProvider(s.groq_api_key.get_secret_value(), s.groq_model, client=client),
        "gemini": GeminiProvider(s.gemini_api_key.get_secret_value(), s.gemini_model, client=client),
    }
    config = RouterConfig(
        timeout=s.llm_timeout_seconds, max_output_tokens=s.llm_max_output_tokens, retry_attempts=s.llm_retry_attempts,
        backoff_base=s.llm_backoff_base_seconds, backoff_cap=s.llm_backoff_cap_seconds, max_retry_after=s.llm_max_retry_after_seconds,
        breaker_failures=s.llm_breaker_failures, breaker_open_seconds=s.llm_breaker_open_seconds,
        tokens_per_minute={"groq": s.groq_tokens_per_minute, "gemini": s.gemini_tokens_per_minute},
        requests_per_minute={"groq": s.groq_requests_per_minute, "gemini": s.gemini_requests_per_minute},
    )  # fmt: skip
    return LLMRouter(providers, config)


def get_router() -> LLMRouter:
    global _router
    with _lock:
        if _router is None:
            _router = build_router()
        return _router


def reset_router() -> None:
    """Forget the shared router (tests, or after the settings changed)."""
    global _router
    with _lock:
        _router = None


def llm_available() -> bool:
    """True when at least one provider has a key."""
    return any(p.configured for p in get_router().providers.values())


def context_for(org_id: uuid.UUID, job_id: uuid.UUID | None, factory: sessionmaker[Session] | None = None) -> LlmContext:
    return LlmContext(DbStore(org_id, factory), job_id)
