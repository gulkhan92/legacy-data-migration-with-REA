"""Rate limit tracking, circuit breaker and backoff. All take an injectable clock, sleep and random source, so tests need
no real waiting."""

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass


# ------------------------------------------------------------------ rate limits


def parse_reset(value: str | None) -> float | None:
    """Groq reset headers: seconds with decimals ("7.66s"), or minutes and seconds ("2m59.56s"), or milliseconds ("120ms")."""
    if not value:
        return None
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?", value.strip())
    if not match or not any(match.groups()):
        try:
            return float(value)
        except ValueError:
            return None
    hours, minutes, seconds, millis = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0) + float(millis or 0) / 1000


@dataclass
class RateInfo:
    """What a provider told us about its limits in a response."""

    remaining_tokens: int | None = None
    remaining_requests: int | None = None
    reset_tokens_seconds: float | None = None
    reset_requests_seconds: float | None = None


class TokenBucket:
    """A local per minute limit for one provider.

    It starts full at the configured limits and refills linearly over a minute. When a response carries the provider's own
    figures (Groq: x-ratelimit-remaining-tokens and x-ratelimit-remaining-requests) they replace the local estimate, so the
    bucket follows what the provider really counts. Request limits that the provider reports per day are not mixed in.
    """

    def __init__(self, tokens_per_minute: int, requests_per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.tokens_per_minute = tokens_per_minute
        self.requests_per_minute = requests_per_minute
        self.clock = clock
        self.tokens = float(tokens_per_minute)
        self.requests = float(requests_per_minute)
        self._stamp = clock()
        self._blocked_until = 0.0
        self.seeded_from_headers = False

    def _refill(self) -> None:
        now = self.clock()
        if now < self._blocked_until:  # waiting out a provider imposed pause: nothing refills yet
            return
        if self._blocked_until > self._stamp:
            self._stamp = self._blocked_until
        elapsed = max(0.0, now - self._stamp)
        self._stamp = now
        self.tokens = min(self.tokens_per_minute, self.tokens + elapsed * self.tokens_per_minute / 60)
        self.requests = min(self.requests_per_minute, self.requests + elapsed * self.requests_per_minute / 60)

    def available(self) -> tuple[float, float]:
        self._refill()
        return self.tokens, self.requests

    def can_send(self, tokens: int) -> bool:
        available_tokens, available_requests = self.available()
        return available_tokens >= tokens and available_requests >= 1

    def wait_seconds(self, tokens: int) -> float:
        """How long until a request of this size would fit (0 when it fits now)."""
        available_tokens, available_requests = self.available()
        waits = [0.0]
        if available_tokens < tokens:
            waits.append((tokens - available_tokens) * 60 / self.tokens_per_minute)
        if available_requests < 1:
            waits.append((1 - available_requests) * 60 / self.requests_per_minute)
        return max(waits)

    def reserve(self, tokens: int) -> None:
        self._refill()
        self.tokens -= tokens
        self.requests -= 1

    def settle(self, reserved: int, actual: int) -> None:
        """Replace the reserved estimate by the real usage."""
        self.tokens = min(self.tokens_per_minute, self.tokens + reserved - actual)

    def update(self, info: RateInfo | None) -> None:
        if info is None:
            return
        self._refill()
        if info.remaining_tokens is not None:
            self.tokens = float(min(self.tokens_per_minute, max(0, info.remaining_tokens)))
            self.seeded_from_headers = True
        if info.remaining_requests is not None and info.remaining_requests <= self.requests_per_minute:
            self.requests = float(max(0, info.remaining_requests))

    def penalise(self, seconds: float | None) -> None:
        """After a 429: nothing fits until the provider's wait is over."""
        self._refill()
        self.tokens = 0.0
        if seconds is not None and seconds > 0:
            self._blocked_until = self.clock() + seconds  # refill starts after the wait


# ------------------------------------------------------------------ circuit breaker


class CircuitBreaker:
    """Opens after `failures` consecutive failures and stays open for `open_seconds`, then lets one trial request through
    (half open). A success closes it, a failure opens it again."""

    def __init__(self, failures: int = 3, open_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.failures_to_open = failures
        self.open_seconds = open_seconds
        self.clock = clock
        self.consecutive_failures = 0
        self.opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self.opened_at >= self.open_seconds else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial_in_flight:
            self._trial_in_flight = True
            return True
        return False

    def success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self._trial_in_flight = False

    def failure(self) -> None:
        self._trial_in_flight = False
        self.consecutive_failures += 1
        if self.opened_at is not None or self.consecutive_failures >= self.failures_to_open:
            self.opened_at = self.clock()

    def retry_in(self) -> float:
        if self.opened_at is None:
            return 0.0
        return max(0.0, self.open_seconds - (self.clock() - self.opened_at))


# ------------------------------------------------------------------ backoff


def backoff_delay(attempt: int, base: float, cap: float, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter: a random delay between half and all of min(cap, base * 2^attempt)."""
    ceiling = min(cap, base * (2 ** attempt))
    return (rng or random).uniform(ceiling / 2, ceiling)
