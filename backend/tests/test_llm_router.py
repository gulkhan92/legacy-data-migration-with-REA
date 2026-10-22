"""The LLM router: failover, retries, repair, cache, budgets, breaker and rate limits, all with fake providers (no network)."""

import json
import logging
import uuid

import pytest

from app.db.models.enums import LlmCallStatus
from app.llm import tasks
from app.llm.adapters import layout_resolver, mapping_resolver
from app.llm.errors import (
    AllProvidersFailed,
    BudgetExceeded,
    PayloadRejected,
    ProviderClientError,
    ProviderServerError,
    ProviderTimeout,
    RateLimited,
)
from app.llm.resilience import RateInfo
from app.llm.router import cache_key
from app.llm.store import OrgLlmSettings
from app.llm.tokens import estimate_json_tokens, estimate_tokens
from app.mapping import llm_hook
from app.mapping.decide import AUTO, NEEDS_LLM, REVIEW, Decision
from tests.llm_helpers import FakeProvider, answer, make_context, make_router, sample_payload, text_response

TASK = "resolve_mappings"


def two(groq_script, gemini_script, **config):  # type: ignore[no-untyped-def]
    groq, gemini = FakeProvider("groq", groq_script, model="openai/gpt-oss-20b"), FakeProvider("gemini", gemini_script, model="gemini-2.5-flash-lite")
    router, clock = make_router({"groq": groq, "gemini": gemini}, **config)
    return router, groq, gemini, clock


# ------------------------------------------------------------------ the normal path


def test_first_provider_answers_and_everything_is_recorded() -> None:
    payload = sample_payload(2)
    router, groq, gemini, _ = two([answer(payload, tokens_in=700, tokens_out=120, cached=500)], [])
    ctx, store = make_context()
    result = router.complete_json(TASK, payload, ctx)
    assert result.provider == "groq" and not result.cached and result.tokens == 820 and result.output.answers[0].target == "x.a"  # type: ignore[attr-defined]
    assert groq.calls == 1 and gemini.calls == 0
    [call] = store.calls
    assert (call.provider, call.task, call.status, call.tokens_in, call.tokens_out, call.cached_tokens) == ("groq", TASK, LlmCallStatus.OK, 700, 120, 500)
    assert call.prompt_version == "resolve_mappings.v1" and call.job_id == ctx.job_id and call.latency_ms == 120 and not call.cache_hit
    assert store.job_tokens[ctx.job_id] == 820 and len(store.cache) == 1


def test_requests_use_the_constant_prefix_temperature_zero_and_the_output_cap() -> None:
    payload = sample_payload(1)
    router, groq, _, _ = two([answer(payload)], [])
    router.complete_json(TASK, payload, make_context()[0])
    request = groq.requests[0]
    assert [m.role for m in request.messages] == ["system", "user"]
    assert request.messages[0].text == tasks.TASKS[TASK].prompt.text  # the constant part comes first and carries no data
    assert json.loads(request.messages[1].text) == payload
    assert request.max_output_tokens == 800 and request.timeout == 30.0 and request.schema_name == TASK
    assert request.schema == tasks.strict_schema(tasks.ResolveMappingsOutput)
    assert "Rock" not in request.messages[0].text


# ------------------------------------------------------------------ cache


def test_a_repeated_request_is_answered_from_the_cache_with_zero_tokens() -> None:
    payload = sample_payload(2)
    router, groq, gemini, _ = two([answer(payload)], [])
    ctx, store = make_context()
    first = router.complete_json(TASK, payload, ctx)
    second = router.complete_json(TASK, payload, ctx)
    assert second.cached and second.provider == "cache" and second.tokens == 0 and second.data == first.data
    assert groq.calls == 1 and gemini.calls == 0
    hit = store.calls[-1]
    assert hit.cache_hit and hit.status is LlmCallStatus.CACHE_HIT and hit.tokens_in == 0 and hit.tokens_out == 0
    assert store.job_tokens[ctx.job_id] == 820 and sum(c.tokens_in + c.tokens_out for c in store.calls if c.cache_hit) == 0
    assert list(store.cache_hits.values()) == [1]


def test_the_cache_key_ignores_noise_but_not_content_prompt_or_task(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    spec = tasks.TASKS[TASK]
    base = sample_payload(2)
    noisy = json.loads(json.dumps(base))
    noisy["columns"][0]["candidates"][0]["similarity"] = 0.6612  # a rounding difference between runs
    shuffled = {"columns": [{k: c[k] for k in reversed(list(c))} for c in base["columns"]]}
    before = cache_key(spec, base)
    assert before == cache_key(spec, noisy) == cache_key(spec, shuffled)
    changed = sample_payload(2)
    changed["columns"][1]["column"]["name"] = "something else"
    assert cache_key(spec, changed) != cache_key(spec, base)
    assert cache_key(tasks.TASKS["interpret_layout"], base) != cache_key(spec, base)
    new_prompt = tasks.Prompt(TASK, 1, spec.prompt.text + " Be brief.", "0" * 64)
    monkeypatch.setattr(tasks, "load_prompt", lambda t, v: new_prompt)
    assert cache_key(spec, base) != before  # an edited prompt never serves old answers


def test_a_damaged_cache_entry_is_ignored_and_replaced() -> None:
    payload = sample_payload(2)
    router, groq, _, _ = two([answer(payload)], [])
    ctx, store = make_context()
    store.cache_put(cache_key(tasks.TASKS[TASK], payload), TASK, "resolve_mappings.v1", {"answers": [{"id": "T.c0"}]}, 100)
    result = router.complete_json(TASK, payload, ctx)
    assert not result.cached and groq.calls == 1


def test_cached_answers_are_checked_against_the_current_request() -> None:
    payload = sample_payload(2)
    router, groq, _, _ = two([answer(payload)], [])
    ctx, store = make_context()
    bad = {"answers": [{"id": "T.c0", "target": "not.offered", "confidence": 0.9, "hint": None}, {"id": "T.c1", "target": None, "confidence": 0.9, "hint": None}]}
    store.cache_put(cache_key(tasks.TASKS[TASK], payload), TASK, "v1", bad, 100)
    assert not router.complete_json(TASK, payload, ctx).cached


# ------------------------------------------------------------------ retries and failover


def test_a_short_rate_limit_is_waited_out_on_the_same_provider() -> None:
    payload = sample_payload(1)
    router, groq, gemini, clock = two([RateLimited("429", retry_after=2.0), answer(payload)], [])
    ctx, store = make_context()
    result = router.complete_json(TASK, payload, ctx)
    assert result.provider == "groq" and groq.calls == 2 and gemini.calls == 0
    assert len(clock.sleeps) == 1 and clock.sleeps[0] >= 2.0  # at least what Retry-After asked for
    assert [c.status for c in store.calls] == [LlmCallStatus.RATE_LIMITED, LlmCallStatus.OK]


def test_a_long_rate_limit_fails_over_without_waiting() -> None:
    payload = sample_payload(1)
    router, groq, gemini, clock = two([RateLimited("429", retry_after=3600.0)], [answer(payload, model="gemini-2.5-flash-lite")])
    ctx, store = make_context()
    result = router.complete_json(TASK, payload, ctx)
    assert result.provider == "gemini" and groq.calls == 1 and gemini.calls == 1 and clock.sleeps == []
    assert [(c.provider, c.status) for c in store.calls] == [("groq", LlmCallStatus.RATE_LIMITED), ("gemini", LlmCallStatus.OK)]
    assert result.attempts[0] == {"provider": "groq", "outcome": "failed", "reason": "rate_limited"}
    assert router.buckets["groq"].available()[0] == 0  # the bucket now knows groq has nothing to give


def test_simulated_429_and_timeouts_fail_over_without_losing_the_request() -> None:
    payload = sample_payload(2)
    router, groq, gemini, clock = two([RateLimited("429", retry_after=1.0), ProviderTimeout("slow")], [answer(payload)])
    ctx, store = make_context()
    result = router.complete_json(TASK, payload, ctx)
    assert result.provider == "gemini" and result.output.answers[1].id == "T.c1"  # type: ignore[attr-defined]
    assert groq.calls == 2 and len(clock.sleeps) == 1  # one backoff between the two groq tries, none before failing over
    assert [c.status for c in store.calls] == [LlmCallStatus.RATE_LIMITED, LlmCallStatus.TIMEOUT, LlmCallStatus.OK]
    assert store.calls[1].error_code == "timeout" and store.job_tokens[ctx.job_id] == 820


def test_server_errors_are_retried_with_exponential_backoff_then_failed_over() -> None:
    payload = sample_payload(1)
    router, groq, gemini, clock = two([ProviderServerError("503"), ProviderServerError("503")], [answer(payload)], retry_attempts=2)
    result = router.complete_json(TASK, payload, make_context()[0])
    assert result.provider == "gemini" and groq.calls == 2
    assert 0.25 <= clock.sleeps[0] <= 0.5  # base backoff with jitter


def test_more_retries_mean_longer_backoff() -> None:
    payload = sample_payload(1)
    router, groq, _, clock = two([ProviderServerError("x")] * 3 + [answer(payload)], [], retry_attempts=4, breaker_failures=10)
    router.complete_json(TASK, payload, make_context()[0])
    assert [round(s, 2) for s in clock.sleeps] != sorted(clock.sleeps, reverse=True) and clock.sleeps[1] > clock.sleeps[0] / 2
    assert clock.sleeps[2] >= 1.0  # the third delay comes from a ceiling of 2 seconds


def test_client_errors_are_not_retried() -> None:
    payload = sample_payload(1)
    router, groq, gemini, clock = two([ProviderClientError("bad key")], [answer(payload)])
    result = router.complete_json(TASK, payload, make_context()[0])
    assert result.provider == "gemini" and groq.calls == 1 and clock.sleeps == []


def test_when_every_provider_fails_the_error_says_what_happened() -> None:
    payload = sample_payload(1)
    router, groq, gemini, _ = two([ProviderTimeout("t"), ProviderTimeout("t")], [ProviderServerError("5"), ProviderServerError("5")])
    ctx, store = make_context()
    with pytest.raises(AllProvidersFailed) as info:
        router.complete_json(TASK, payload, ctx)
    assert [(a["provider"], a["reason"]) for a in info.value.attempts] == [("groq", "timeout"), ("gemini", "server_error")]
    assert all(c.status is not LlmCallStatus.OK for c in store.calls) and len(store.calls) == 4
    assert store.job_tokens.get(ctx.job_id, 0) == 0 and not store.cache  # nothing charged, nothing cached


def test_provider_order_comes_from_the_organization_settings() -> None:
    payload = sample_payload(1)
    router, groq, gemini, _ = two([answer(payload)], [answer(payload)])
    ctx, _ = make_context(order=["gemini", "groq"])
    assert router.complete_json(TASK, payload, ctx).provider == "gemini" and groq.calls == 0
    ctx, _ = make_context(order=["gemini"])
    router2, groq2, gemini2, _ = two([answer(payload)], [answer(payload)])
    assert router2.complete_json(TASK, payload, ctx).provider == "gemini" and groq2.calls == 0


def test_a_provider_without_a_key_is_skipped() -> None:
    payload = sample_payload(1)
    groq = FakeProvider("groq", [], configured=False)
    gemini = FakeProvider("gemini", [answer(payload)])
    router, _ = make_router({"groq": groq, "gemini": gemini})
    result = router.complete_json(TASK, payload, make_context()[0])
    assert result.provider == "gemini" and groq.calls == 0 and result.attempts[0] == {"provider": "groq", "outcome": "skipped", "reason": "not configured"}


# ------------------------------------------------------------------ schema failures and repair


def test_invalid_json_gets_one_repair_request_that_shows_the_problem() -> None:
    payload = sample_payload(2)
    router, groq, gemini, _ = two([text_response("Sure, here you go: T.c0 -> x.a"), lambda r: answer(payload)], [])
    ctx, store = make_context()
    result = router.complete_json(TASK, payload, ctx)
    assert result.provider == "groq" and groq.calls == 2 and gemini.calls == 0 and result.attempts[-1]["repaired"] is True
    repair = groq.requests[1].messages
    assert [m.role for m in repair] == ["system", "user", "assistant", "user"] and "Sure, here you go" in repair[2].text
    assert "not valid" in repair[3].text and "JSON only" in repair[3].text
    assert [c.status for c in store.calls] == [LlmCallStatus.SCHEMA_ERROR, LlmCallStatus.OK]
    assert store.job_tokens[ctx.job_id] == 760 + 820  # the wasted request is charged too


def test_a_fenced_answer_is_accepted_without_a_repair() -> None:
    payload = sample_payload(1)
    good = answer(payload)
    fenced = text_response("```json\n" + good.text + "\n```")
    router, groq, _, _ = two([fenced], [])
    assert router.complete_json(TASK, payload, make_context()[0]).provider == "groq" and groq.calls == 1


def test_an_answer_naming_a_field_that_was_not_offered_is_repaired() -> None:
    payload = sample_payload(2)
    wrong = text_response(json.dumps({"answers": [{"id": "T.c0", "target": "invented.field", "confidence": 0.9, "hint": None},
                                                  {"id": "T.c1", "target": "x.b", "confidence": 0.8, "hint": None}]}))  # fmt: skip
    router, groq, _, _ = two([wrong, answer(payload)], [])
    result = router.complete_json(TASK, payload, make_context()[0])
    assert result.attempts[-1]["repaired"] and "invented.field" in groq.requests[1].messages[3].text


def test_a_missing_answer_is_repaired() -> None:
    payload = sample_payload(3)
    partial = text_response(json.dumps({"answers": [{"id": "T.c0", "target": "x.a", "confidence": 0.9, "hint": None}]}))
    router, groq, _, _ = two([partial, answer(payload)], [])
    router.complete_json(TASK, payload, make_context()[0])
    assert groq.calls == 2 and "exactly one answer" in groq.requests[1].messages[3].text


def test_an_answer_cut_off_by_the_output_limit_is_a_failure_that_is_repaired() -> None:
    payload = sample_payload(1)
    cut = text_response('{"answers": [{"id": "T.c0", "tar', finish="length", tokens_out=800)
    router, groq, _, _ = two([cut, answer(payload)], [])
    router.complete_json(TASK, payload, make_context()[0])
    assert "output limit" in groq.requests[1].messages[3].text


def test_two_bad_answers_fail_over_to_the_next_provider() -> None:
    payload = sample_payload(1)
    bad = text_response("nonsense")
    router, groq, gemini, _ = two([bad, text_response("still nonsense")], [answer(payload)])
    ctx, store = make_context()
    result = router.complete_json(TASK, payload, ctx)
    assert result.provider == "gemini" and groq.calls == 2 and gemini.calls == 1  # one repair at most per provider
    assert [c.status for c in store.calls] == [LlmCallStatus.SCHEMA_ERROR, LlmCallStatus.SCHEMA_ERROR, LlmCallStatus.OK]
    assert router.breakers["groq"].consecutive_failures == 1


def test_a_repair_that_the_budget_cannot_afford_stops_the_whole_request() -> None:
    payload = sample_payload(1)
    router, groq, gemini, _ = two([text_response("nonsense")], [answer(payload)])
    ctx, store = make_context(job_budget=1900)  # room for one request (about 1500 worst case), not for a second one after 760 are spent
    with pytest.raises(BudgetExceeded) as info:
        router.complete_json(TASK, payload, ctx)
    assert info.value.scope == "job_tokens" and groq.calls == 1 and gemini.calls == 0


# ------------------------------------------------------------------ circuit breaker and rate limit bucket


def echo(request):  # type: ignore[no-untyped-def]
    """A fake provider answer that fits whatever request arrives."""
    return answer(json.loads(request.messages[1].text), tokens_in=100, tokens_out=20)


def test_three_consecutive_failures_open_the_circuit_for_sixty_seconds() -> None:
    router, groq, gemini, clock = two([ProviderServerError("x")] * 3, [echo] * 4, retry_attempts=1)
    for i in range(3):
        result = router.complete_json(TASK, sample_payload(1, offset=i * 10), make_context()[0])  # groq fails each time, gemini answers
        assert result.provider == "gemini"
    assert router.breakers["groq"].state == "open" and groq.calls == 3
    fourth = router.complete_json(TASK, sample_payload(1, offset=40), make_context()[0])  # groq is not even tried
    assert fourth.provider == "gemini" and groq.calls == 3 and fourth.attempts[0] == {"provider": "groq", "outcome": "skipped", "reason": "circuit open"}
    assert router.status()["groq"]["circuit"] == "open"


def test_the_circuit_closes_again_after_a_successful_trial() -> None:
    router, groq, gemini, clock = two([ProviderServerError("x")] * 3, [], retry_attempts=1)
    ctx, _ = make_context(order=["groq"])
    for i in range(3):
        with pytest.raises(AllProvidersFailed):
            router.complete_json(TASK, sample_payload(1, offset=i), ctx)
    with pytest.raises(AllProvidersFailed) as skipped:
        router.complete_json(TASK, sample_payload(1, offset=9), ctx)
    assert skipped.value.attempts == [{"provider": "groq", "outcome": "skipped", "reason": "circuit open"}] and groq.calls == 3
    clock.advance(61)
    payload = sample_payload(1, offset=20)
    groq.script.append(answer(payload))
    assert router.complete_json(TASK, payload, ctx).provider == "groq" and router.breakers["groq"].state == "closed"


def test_a_provider_with_no_room_in_its_rate_limit_bucket_is_skipped() -> None:
    payload = sample_payload(1)
    router, groq, gemini, _ = two([answer(payload)], [answer(payload)])
    router.buckets["groq"].tokens = 100  # almost nothing left this minute
    result = router.complete_json(TASK, payload, make_context()[0])
    assert result.provider == "gemini" and groq.calls == 0 and result.attempts[0]["reason"] == "rate limit"


def test_response_headers_keep_the_bucket_in_step_with_the_provider() -> None:
    first, second = sample_payload(1), sample_payload(1, offset=5)
    low = RateInfo(remaining_tokens=300, remaining_requests=20)
    router, groq, gemini, _ = two([answer(first, rate=low)], [answer(second)])
    router.complete_json(TASK, first, make_context()[0])
    assert router.buckets["groq"].available()[0] == pytest.approx(300, abs=5) and router.buckets["groq"].seeded_from_headers
    assert router.complete_json(TASK, second, make_context()[0]).provider == "gemini"  # groq says 300 tokens are left


def test_usage_settles_the_bucket_with_cached_prompt_tokens_excluded() -> None:
    payload = sample_payload(1)
    router, groq, _, _ = two([answer(payload, tokens_in=1000, tokens_out=100, cached=800)], [])
    router.complete_json(TASK, payload, make_context()[0])
    assert router.buckets["groq"].available()[0] == pytest.approx(8000 - (200 + 100), abs=5)  # cached tokens do not count against the limit


# ------------------------------------------------------------------ budgets


def test_a_runaway_job_is_stopped_by_its_budget_before_anything_is_sent() -> None:
    router, groq, gemini, _ = two([], [])
    ctx, store = make_context(job_budget=6000, max_calls=100)
    sent = 0
    with pytest.raises(BudgetExceeded) as stop:
        for i in range(50):  # a loop that keeps asking different questions
            payload = sample_payload(4, offset=i * 10)
            groq.script.append(answer(payload, tokens_in=1400, tokens_out=200))
            router.complete_json(TASK, payload, ctx)
            sent += 1
    assert stop.value.scope == "job_tokens" and 2 <= sent <= 4
    assert store.job_tokens[ctx.job_id] <= 6000  # the budget is never exceeded
    assert groq.calls == sent  # the refused request never reached a provider
    assert stop.value.detail["used"] + stop.value.detail["needed"] > 6000


def test_a_job_may_make_at_most_two_llm_calls_by_default() -> None:
    router, groq, _, _ = two([], [])
    ctx, store = make_context()
    for i in range(2):
        payload = sample_payload(1, offset=i)
        groq.script.append(answer(payload, tokens_in=300, tokens_out=50))
        router.complete_json(TASK, payload, ctx)
    with pytest.raises(BudgetExceeded) as stop:
        router.complete_json(TASK, sample_payload(1, offset=9), ctx)
    assert stop.value.scope == "job_calls" and groq.calls == 2 and store.job_tokens[ctx.job_id] == 700


def test_the_organization_daily_budget_applies_across_jobs() -> None:
    router, groq, _, _ = two([], [])
    ctx, store = make_context(daily=3000)
    store.daily_tokens = 2500
    with pytest.raises(BudgetExceeded) as stop:
        router.complete_json(TASK, sample_payload(2), ctx)
    assert stop.value.scope == "org_daily_tokens" and groq.calls == 0
    other_job, _ = make_context(daily=100_000)
    groq.script.append(answer(sample_payload(2)))
    assert router.complete_json(TASK, sample_payload(2), other_job).provider == "groq"


def test_calls_without_a_job_use_only_the_daily_budget() -> None:
    router, groq, _, _ = two([], [])
    ctx, store = make_context(job=False)
    payload = sample_payload(1)
    groq.script.append(answer(payload))
    assert router.complete_json(TASK, payload, ctx).provider == "groq" and store.daily_tokens == 820


def test_an_exhausted_budget_still_serves_cached_answers() -> None:
    payload = sample_payload(2)
    router, groq, _, _ = two([answer(payload, tokens_in=2900, tokens_out=300)], [])
    ctx, store = make_context(job_budget=6000, max_calls=1)
    router.complete_json(TASK, payload, ctx)
    with pytest.raises(BudgetExceeded):
        router.complete_json(TASK, sample_payload(2, offset=7), ctx)
    assert router.complete_json(TASK, payload, ctx).cached  # the same question costs nothing, so no budget is needed


def test_the_first_run_of_a_realistic_job_stays_under_6000_tokens() -> None:
    """24 ambiguous columns: two batched requests (12 columns each), the most a job may send."""
    columns = sample_payload(24)["columns"]
    router, groq, _, _ = two([], [])
    ctx, store = make_context()
    decisions = [Decision("T", f"c{i}", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.66), ("x.b", 0.64), ("x.c", 0.61)]) for i in range(24)]

    def reply(request):  # type: ignore[no-untyped-def]
        payload = json.loads(request.messages[1].text)
        usage_in = estimate_tokens(request.messages[0].text) + estimate_json_tokens(payload)
        return answer(payload, tokens_in=usage_in, tokens_out=12 * 25)

    groq.script.extend([reply, reply])
    profiles = {d.key: __import__("tests.test_mapping_units", fromlist=["profile"]).profile(["Rock", "Jazz"] * 20, d.column) for d in decisions}
    info = {k: {"type": "text", "description": f"Description of {k}."} for k in ("x.a", "x.b", "x.c")}
    requests, _ = llm_hook.resolve(decisions, profiles, info, mapping_resolver(router, ctx), __import__("app.mapping.thresholds", fromlist=["x"]).MappingThresholds())
    assert requests == 2 and groq.calls == 2
    assert store.job_tokens[ctx.job_id] < 6000 and all(d.status == AUTO for d in decisions)
    assert [len(json.loads(r.messages[1].text)["columns"]) for r in groq.requests] == [12, 12]
    assert len(columns) == 24


def test_a_repeated_run_of_the_same_job_uses_zero_tokens() -> None:
    router, groq, _, _ = two([], [])
    decisions_first = [Decision("T", f"c{i}", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.66), ("x.b", 0.64)]) for i in range(5)]
    decisions_again = [Decision("T", f"c{i}", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.66), ("x.b", 0.64)]) for i in range(5)]
    from app.mapping.thresholds import MappingThresholds
    from tests.test_mapping_units import profile

    profiles = {d.key: profile(["Rock", "Jazz"] * 20, d.column) for d in decisions_first}
    info = {k: {"type": "text", "description": f"Description of {k}."} for k in ("x.a", "x.b")}
    ctx, store = make_context()
    groq.script.append(lambda r: answer(json.loads(r.messages[1].text), tokens_in=900, tokens_out=100))
    llm_hook.resolve(decisions_first, profiles, info, mapping_resolver(router, ctx), MappingThresholds())
    spent = store.job_tokens[ctx.job_id]
    llm_hook.resolve(decisions_again, profiles, info, mapping_resolver(router, ctx), MappingThresholds())  # the same fixture again
    assert spent == 1000 and store.job_tokens[ctx.job_id] == spent and groq.calls == 1
    assert [d.status for d in decisions_again] == [AUTO] * 5 and store.calls[-1].cache_hit


# ------------------------------------------------------------------ adapters


def test_the_mapping_adapter_never_raises_and_leaves_columns_for_review() -> None:
    router, groq, gemini, _ = two([ProviderTimeout("t")] * 2, [ProviderTimeout("t")] * 2)
    from app.mapping.thresholds import MappingThresholds
    from tests.test_mapping_units import profile

    decisions = [Decision("T", "c0", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.66), ("x.b", 0.64)])]
    profiles = {"T.c0": profile(["Rock", "Jazz"] * 20, "c0")}
    ctx, _ = make_context()
    info = {k: {"type": "text", "description": "d"} for k in ("x.a", "x.b")}
    llm_hook.resolve(decisions, profiles, info, mapping_resolver(router, ctx), MappingThresholds())
    assert decisions[0].status == REVIEW  # the job is not lost: a person decides


def test_the_layout_adapter_batches_every_ambiguity_into_one_request_and_drops_guesses() -> None:
    from app.discovery.models import Ambiguity

    router, groq, _, _ = two([], [])
    ambiguities = [Ambiguity(kind="unknown_byte_write", method="Vendor.Store.WriteRow", il_offset=20 + i, detail="bytes of unknown width", excerpt=f"ldstr 'F{i}' ; call Vendor.Codec::Pack") for i in range(3)]

    def reply(request):  # type: ignore[no-untyped-def]
        ids = [a["id"] for a in json.loads(request.messages[1].text)["ambiguities"]]
        body = [
            {"id": ids[0], "type": "char", "length": 12, "endianness": None, "confidence": 0.9},
            {"id": ids[1], "type": "int32", "length": 4, "endianness": "big", "confidence": 0.5},  # too unsure to use
            {"id": ids[2], "type": "bytes", "length": None, "endianness": None, "confidence": 0.95},  # an admission of ignorance
        ]
        return text_response(json.dumps({"answers": body}), tokens_in=400, tokens_out=90)

    groq.script.append(reply)
    ctx, store = make_context()
    answers = layout_resolver(router, ctx)(ambiguities)
    assert groq.calls == 1 and list(answers) == ["Vendor.Store.WriteRow@20"] and answers["Vendor.Store.WriteRow@20"]["length"] == 12
    assert store.calls[0].task == "interpret_layout" and store.calls[0].prompt_version == "interpret_layout.v1"
    assert layout_resolver(router, ctx)([]) == {}


def test_the_layout_answer_is_validated_like_any_other() -> None:
    from app.discovery.models import Ambiguity

    router, groq, _, _ = two([], [])
    ambiguity = Ambiguity(kind="unsupported_write", method="M.W", il_offset=5, detail="d", excerpt="e")
    bad = text_response(json.dumps({"answers": [{"id": "M.W@5", "type": "float128", "length": 16, "endianness": "middle", "confidence": 0.9}]}))
    good = text_response(json.dumps({"answers": [{"id": "M.W@5", "type": "int16", "length": 2, "endianness": "little", "confidence": 0.9}]}))
    groq.script.extend([bad, good])
    result = layout_resolver(router, make_context()[0])([ambiguity])
    assert result == {"M.W@5": {"type": "int16", "length": 2, "endianness": "little", "confidence": 0.9}} and groq.calls == 2


# ------------------------------------------------------------------ payload guard


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["columns"][0]["column"]["samples"].append("luis@embraer.com.br"),
        lambda p: p["columns"][0]["column"]["samples"].append("999-10-8066"),
        lambda p: p["columns"][0]["column"]["samples"].append("4111 1111 1111 1111"),
        lambda p: p["columns"][0]["column"]["samples"].append("+55 (12) 3923-5555"),
        lambda p: p["columns"][0]["column"]["samples"].append("1234567890123"),
        lambda p: p["columns"][0].__setitem__("rows", [{"a": 1}]),
        lambda p: p["columns"][0]["column"].__setitem__("sample_rows", []),
        lambda p: p.__setitem__("api_key", "x"),
    ],
)
def test_the_guard_refuses_payloads_that_must_not_leave_the_system(mutation) -> None:  # type: ignore[no-untyped-def]
    payload = sample_payload(1)
    mutation(payload)
    router, groq, _, _ = two([], [])
    with pytest.raises(PayloadRejected):
        router.complete_json(TASK, payload, make_context()[0])
    assert groq.calls == 0


def test_the_guard_accepts_masked_values_and_refuses_oversized_payloads() -> None:
    payload = sample_payload(1)
    payload["columns"][0]["column"]["samples"] = ["9999999999", "ACCT-999999999999"]  # digit runs the profile view already masked
    router, groq, _, _ = two([answer(payload)], [])
    assert router.complete_json(TASK, payload, make_context()[0]).provider == "groq"
    huge = sample_payload(300)
    with pytest.raises(PayloadRejected) as info:
        router.complete_json(TASK, huge, make_context()[0])
    assert "larger than" in info.value.message


# ------------------------------------------------------------------ prompts, connection test, privacy of logs


def test_prompts_are_versioned_files_with_a_constant_prefix() -> None:
    for name in tasks.TASKS:
        prompt = tasks.TASKS[name].prompt
        assert prompt.label == f"{name}.v1" and len(prompt.sha256) == 64 and tasks.latest_version(name) == 1
        assert (tasks.PROMPT_DIR / f"{name}.v1.txt").exists()
        assert estimate_tokens(prompt.text) < 600  # the constant part stays small
        assert "{" in prompt.text and "JSON only" in prompt.text
        assert tasks.load_prompt(name, 1) is prompt  # loaded once


def test_test_connection_makes_one_request_without_failover_and_without_the_cache() -> None:
    router, groq, gemini, _ = two([text_response('{"ok": true}', tokens_in=40, tokens_out=8)], [])
    ctx, store = make_context(job=False)
    result = router.test_connection("groq", ctx)
    assert result["ok"] and result["provider"] == "groq" and result["tokens"] == 48 and groq.calls == 1
    assert store.calls[0].task == "connection_test" and store.calls[0].status is LlmCallStatus.OK and not store.cache
    broken, groq2, gemini2, _ = two([ProviderClientError("401")], [answer(sample_payload(1))])
    failed = broken.test_connection("groq", make_context(job=False)[0])
    assert failed["ok"] is False and failed["error"] == "client_error" and gemini2.calls == 0  # an unhealthy provider is reported, not hidden


def test_nothing_sensitive_is_logged_even_when_everything_fails(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    payload = sample_payload(1)
    payload["columns"][0]["column"]["samples"] = ["VERY-SECRET-SAMPLE"]
    router, groq, gemini, _ = two([ProviderTimeout("t"), ProviderTimeout("t")], [ProviderServerError("5"), ProviderServerError("5")])
    from app.mapping.thresholds import MappingThresholds
    from tests.test_mapping_units import profile

    decisions = [Decision("T", "c0", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.66)])]
    llm_hook.resolve(decisions, {"T.c0": profile(["VERY-SECRET-SAMPLE"] * 40, "c0")}, {"x.a": {"type": "text", "description": "d"}}, mapping_resolver(router, make_context()[0]), MappingThresholds())
    assert "VERY-SECRET-SAMPLE" not in caplog.text and "SYSTEM" not in caplog.text
    assert any("unanswered" in r.message for r in caplog.records)


def test_status_view_has_no_secrets_and_shows_the_limits() -> None:
    router, *_ = two([], [])
    status = router.status()
    assert set(status) == {"groq", "gemini"} and status["groq"]["circuit"] == "closed" and status["groq"]["tokens_available"] == 8000
    assert "key" not in json.dumps(status).lower().replace("api_key_", "")
    assert uuid.UUID(int=0)  # keep the import used
