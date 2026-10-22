"""Rate limit bucket, circuit breaker and backoff, driven by a manual clock."""

import random

import pytest

from app.llm.resilience import CircuitBreaker, RateInfo, TokenBucket, backoff_delay
from tests.llm_helpers import Clock


def test_the_bucket_starts_full_and_refills_over_a_minute() -> None:
    clock = Clock()
    bucket = TokenBucket(8000, 30, clock)
    assert bucket.can_send(8000) and not bucket.can_send(8001)
    bucket.reserve(6000)
    assert not bucket.can_send(3000) and bucket.can_send(2000)
    clock.advance(30)  # half a minute gives back half the limit
    assert bucket.available()[0] == pytest.approx(2000 + 4000)
    clock.advance(600)
    assert bucket.available() == (8000, 30)  # never above the limit


def test_wait_time_for_a_request_that_does_not_fit_yet() -> None:
    clock = Clock()
    bucket = TokenBucket(6000, 30, clock)
    bucket.reserve(6000)
    assert bucket.wait_seconds(3000) == pytest.approx(30.0)
    assert bucket.wait_seconds(0) == 0.0


def test_the_request_limit_counts_too() -> None:
    clock = Clock()
    bucket = TokenBucket(100_000, 2, clock)
    bucket.reserve(10)
    bucket.reserve(10)
    assert not bucket.can_send(10) and bucket.wait_seconds(10) > 0
    clock.advance(30)
    assert bucket.can_send(10)


def test_response_headers_replace_the_local_estimate() -> None:
    clock = Clock()
    bucket = TokenBucket(8000, 30, clock)
    bucket.reserve(5000)
    bucket.update(RateInfo(remaining_tokens=7216, remaining_requests=28))
    assert bucket.available() == (7216, 28) and bucket.seeded_from_headers
    bucket.update(RateInfo(remaining_tokens=120))
    assert not bucket.can_send(500)  # the provider says only 120 tokens are left this minute
    bucket.update(RateInfo(remaining_requests=14_990))  # a per day figure is not a per minute figure and is ignored
    assert bucket.available()[1] <= 30
    bucket.update(None)


def test_reserved_tokens_are_settled_against_real_usage() -> None:
    bucket = TokenBucket(8000, 30, Clock())
    bucket.reserve(2300)
    bucket.settle(2300, 730)  # the call used far less than the pessimistic estimate
    assert bucket.available()[0] == pytest.approx(8000 - 730)


def test_a_rate_limit_response_empties_the_bucket_until_the_wait_is_over() -> None:
    clock = Clock()
    bucket = TokenBucket(8000, 30, clock)
    bucket.penalise(20)
    assert not bucket.can_send(100)
    clock.advance(19)
    assert not bucket.can_send(100)
    clock.advance(10)
    assert bucket.can_send(100)


# ------------------------------------------------------------------ breaker


def test_the_breaker_opens_after_three_consecutive_failures_for_sixty_seconds() -> None:
    clock = Clock()
    breaker = CircuitBreaker(3, 60.0, clock)
    assert breaker.state == "closed" and breaker.allow()
    breaker.failure()
    breaker.failure()
    assert breaker.state == "closed"
    breaker.failure()
    assert breaker.state == "open" and not breaker.allow() and breaker.retry_in() == pytest.approx(60.0)
    clock.advance(59)
    assert breaker.state == "open" and not breaker.allow()
    clock.advance(2)
    assert breaker.state == "half_open"


def test_a_success_in_between_resets_the_count() -> None:
    breaker = CircuitBreaker(3, 60.0, Clock())
    breaker.failure()
    breaker.failure()
    breaker.success()
    breaker.failure()
    breaker.failure()
    assert breaker.state == "closed"


def test_half_open_lets_one_trial_through() -> None:
    clock = Clock()
    breaker = CircuitBreaker(3, 60.0, clock)
    for _ in range(3):
        breaker.failure()
    clock.advance(61)
    assert breaker.allow() and not breaker.allow()  # a single trial request
    breaker.success()
    assert breaker.state == "closed" and breaker.allow() and breaker.allow()


def test_a_failed_trial_opens_the_breaker_again() -> None:
    clock = Clock()
    breaker = CircuitBreaker(3, 60.0, clock)
    for _ in range(3):
        breaker.failure()
    clock.advance(61)
    assert breaker.allow()
    breaker.failure()
    assert breaker.state == "open" and breaker.retry_in() == pytest.approx(60.0)


# ------------------------------------------------------------------ backoff


def test_backoff_grows_exponentially_with_jitter_and_a_cap() -> None:
    rng = random.Random(1)
    for attempt, ceiling in enumerate([0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]):
        for _ in range(50):
            delay = backoff_delay(attempt, 0.5, 8.0, rng)
            assert ceiling / 2 <= delay <= ceiling
    delays = {backoff_delay(2, 0.5, 8.0, rng) for _ in range(20)}
    assert len(delays) > 10  # jittered, not a fixed value
    assert backoff_delay(0, 0.5, 8.0, random.Random(5)) == backoff_delay(0, 0.5, 8.0, random.Random(5))  # reproducible with a seed
