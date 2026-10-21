"""Mapping engine unit tests: text, type gate, decisions, thresholds, LLM hand over, embedding service."""

import json

import numpy as np
import pandas as pd
import pytest

from app.mapping import compat, llm_hook, texts
from app.mapping.decide import AUTO, MEMORY, NEEDS_LLM, REVIEW, UNMAPPED, ColumnInput, MemoryHit, TargetInfo, decide
from app.mapping.embedding import DIMENSIONS, EmbeddingService, EmbeddingUnavailable, get_embedding_service
from app.mapping.thresholds import DEFAULT_FILE, MappingThresholds, load_thresholds, save_thresholds
from app.profiling.column_profile import ColumnAccumulator
from app.profiling import pii, semantic
from app.profiling.models import ColumnProfile
from app.profiling.readers import ColumnHint


def profile(values: list, name: str = "col", hint: ColumnHint | None = None) -> ColumnProfile:
    acc = ColumnAccumulator(name, hint or ColumnHint(name))
    acc.update(pd.Series(values, dtype=object))
    column = acc.finalize("T", 0)
    semantic.apply_layout_hints(column)
    semantic.classify(column)
    pii.apply_pii(column)
    return column


def t(**kw) -> MappingThresholds:  # type: ignore[no-untyped-def]
    return MappingThresholds(reject_similarity=0.5, auto_similarity=0.7, auto_margin=0.05, table_min_score=0.4, **kw)


# ------------------------------------------------------------------ texts


def test_source_text_describes_the_column_in_plain_words() -> None:
    p = profile(["Rock", "Jazz", "Metal"] * 20, "cat_nm")
    text = texts.source_text("PROD_MST", p)
    assert text.startswith("table product master | column category name | type coded value")
    assert "examples Rock, Jazz, Metal" in text


def test_source_text_never_holds_values_of_a_pii_column() -> None:
    emails = [f"user{i}@example.com" for i in range(40)]
    text = texts.source_text("CUST_MST", profile(emails, "email_adr"))
    assert "examples" not in text and "@" not in text and "email address" in text
    names = texts.source_text("CUST_MST", profile(["Holy", "Philips"] * 20, "l_nm"))
    assert "Holy" not in names and "Philips" not in names


def test_source_text_mentions_the_referenced_table_and_the_note() -> None:
    p = profile(list(range(1, 100)), "cust_no")
    p.references, p.semantic_type = "CUST_MST.cust_no", "reference"
    assert "refers to customer master" in texts.source_text("INV_HDR", p, note="bit 0 male")
    assert "bit 0 male" in texts.source_text("INV_HDR", p, note="bit 0 male")


def test_target_text_has_table_name_type_description_and_synonyms() -> None:
    field = {"table": "order_items", "name": "unit_price", "type": "decimal", "description": "Price of one unit.", "synonyms": ["rate", "cost"]}
    assert texts.target_text(field) == "table order items | field unit price | type decimal | Price of one unit. | also called rate, cost"


# ------------------------------------------------------------------ type gate


@pytest.mark.parametrize(
    "values,name,target,allowed",
    [
        (["2010-01-01"] * 30, "d", "decimal", False), (["2010-01-01"] * 30, "d", "date", True), (["2010-01-01"] * 30, "d", "timestamp", True),
        (["2010-01-01"] * 30, "d", "integer", False), (["2010-01-01"] * 30, "d", "text", False),
        (["1.5", "2.5"] * 20, "price", "decimal", True), (["1.5", "2.5"] * 20, "price", "date", False), (["1.5", "2.5"] * 20, "price", "text", False),
        (["alpha", "beta"] * 20, "x", "date", False), (["alpha", "beta"] * 20, "x", "text", True), (["alpha", "beta"] * 20, "x", "decimal", False),
        (list(range(1, 300)), "cust_no", "text", True), (list(range(1, 300)), "cust_no", "reference", False),
    ],
)
def test_type_compatibility_gate(values: list, name: str, target: str, allowed: bool) -> None:
    assert compat.compatible(profile(values, name), target) is allowed


def test_a_reference_may_only_feed_references_and_a_flag_field_may_feed_text() -> None:
    reference = profile([(i % 50) + 1 for i in range(300)], "cust_no")
    reference.semantic_type = "reference"
    assert compat.compatible(reference, "reference") and not compat.compatible(reference, "text") and not compat.compatible(reference, "date")
    flags = profile(list(range(0, 60)), "Flags", ColumnHint("Flags", "uint16", "bit_flags", "int"))
    assert compat.compatible(flags, "text") and compat.compatible(flags, "boolean") and not compat.compatible(flags, "date")


# ------------------------------------------------------------------ decisions (synthetic similarities)


def targets() -> list[TargetInfo]:
    return [TargetInfo("cust.name", "cust", "name", "text"), TargetInfo("cust.birth", "cust", "birth", "date"), TargetInfo("cust.score", "cust", "score", "decimal"),
            TargetInfo("ord.total", "ord", "total", "decimal"), TargetInfo("ord.placed", "ord", "placed", "date")]  # fmt: skip


def column(table: str, name: str, values: list, sims: list[float], memory: MemoryHit | None = None) -> ColumnInput:
    return ColumnInput(table, profile(values, name), np.array(sims, dtype=np.float32), memory)


TEXT = ["alpha", "beta"] * 20
DATE = ["2010-01-01", "2011-02-03"] * 20
NUM = ["1.5", "2.5"] * 20


def test_the_type_gate_wins_over_a_higher_similarity() -> None:
    # the date column is most similar to the decimal fields, but a date may only map to a date field
    cols = [column("C", "born", DATE, [0.3, 0.72, 0.90, 0.2, 0.2]), column("C", "label", TEXT, [0.85, 0.1, 0.1, 0.1, 0.1])]
    d = {x.column: x for x in decide(cols, targets(), t())}
    assert d["born"].target == "cust.birth" and d["born"].status == AUTO
    assert d["label"].target == "cust.name"


def test_each_field_is_used_once_by_the_column_that_fits_it_best() -> None:
    cols = [column("C", "a", TEXT, [0.90, 0, 0, 0, 0]), column("C", "b", TEXT, [0.80, 0, 0, 0, 0])]
    d = {x.column: x for x in decide(cols, targets(), t())}
    assert d["a"].target == "cust.name" and d["b"].status == UNMAPPED and "better matching columns" in d["b"].reason


def test_thresholds_decide_between_auto_llm_and_unmapped() -> None:
    cols = [
        column("C", "sure", TEXT, [0.92, 0, 0, 0, 0]),
        column("C", "close_call", DATE, [0, 0.75, 0.1, 0.1, 0.1]),
        column("C", "weak", NUM, [0, 0, 0.45, 0, 0]),
    ]
    th = MappingThresholds(reject_similarity=0.5, auto_similarity=0.8, auto_margin=0.0, table_min_score=0.3)
    d = {x.column: x for x in decide(cols, targets(), th)}
    assert d["sure"].status == AUTO and d["close_call"].status == NEEDS_LLM and d["weak"].status == UNMAPPED
    assert d["close_call"].candidates[0][0] == "cust.birth" and d["weak"].target is None


def test_a_free_alternative_with_a_similar_score_makes_a_match_ambiguous() -> None:
    cols = [column("C", "a", NUM, [0, 0, 0.80, 0, 0])]
    sure = decide(cols, targets(), MappingThresholds(reject_similarity=0.5, auto_similarity=0.7, auto_margin=0.05, table_min_score=0.3))
    assert sure[0].status == AUTO  # the date and text fields are incompatible, so nothing competes
    both = [column("C", "a", NUM, [0, 0, 0.80, 0.79, 0])]  # but the other decimal field is in another table
    assert decide(both, targets(), MappingThresholds(reject_similarity=0.5, auto_similarity=0.7, auto_margin=0.05, table_min_score=0.3))[0].status == AUTO


def test_the_target_table_is_chosen_for_the_whole_source_table() -> None:
    cols = [column("S", "a", NUM, [0, 0, 0.55, 0.66, 0]), column("S", "b", DATE, [0, 0.4, 0, 0, 0.9])]
    d = {x.column: x for x in decide(cols, targets(), t())}
    assert d["a"].target == "ord.total" and d["b"].target == "ord.placed"  # the table "ord" fits both columns better
    assert {x.target_table for x in d.values()} == {"ord"}


def test_a_table_that_fits_no_target_table_is_unmapped() -> None:
    cols = [column("LOG", "msg", TEXT, [0.2, 0, 0, 0, 0]), column("LOG", "at", DATE, [0, 0.1, 0, 0, 0.2])]
    assert {x.status for x in decide(cols, targets(), t())} == {UNMAPPED}


def test_memory_hits_are_reused_and_claim_their_field() -> None:
    cols = [column("C", "known", TEXT, [0.1, 0, 0, 0, 0], MemoryHit("cust.name", 0.02)), column("C", "other", TEXT, [0.9, 0, 0, 0, 0])]
    d = {x.column: x for x in decide(cols, targets(), t())}
    assert d["known"].status == MEMORY and d["known"].method == "memory" and d["known"].target == "cust.name" and d["known"].confidence == 0.98
    assert d["other"].target != "cust.name"  # the field is already taken


def test_confidence_mixes_similarity_and_margin() -> None:
    th = MappingThresholds(similarity_weight=0.5, margin_full=0.2)
    from app.mapping.decide import confidence

    assert confidence(0.8, 0.2, th) == 0.9 and confidence(0.8, 0.1, th) == 0.65 and confidence(1.2, 0.5, th) == 1.0


# ------------------------------------------------------------------ thresholds live in config


def test_thresholds_are_loaded_from_the_tuned_file_with_their_provenance() -> None:
    values = load_thresholds()
    data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    assert values.reject_similarity == data["reject_similarity"] and values.auto_similarity == data["auto_similarity"]
    tuning = data["tuning"]
    assert tuning["held_out_set"]["clinic"] == ["COND", "MEDS", "OBS"] and tuning["held_out"]["accuracy"] >= 0.85
    assert set(tuning["tuning_set"]) == {"commerce", "clinic"}


def test_thresholds_round_trip_and_the_settings_path_wins(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings
    from app.mapping import thresholds as module

    path = tmp_path / "custom.json"
    save_thresholds(MappingThresholds(reject_similarity=0.9, auto_similarity=0.95), path)
    monkeypatch.setattr(get_settings(), "mapping_thresholds_file", str(path))
    module._load.cache_clear()
    try:
        assert load_thresholds().reject_similarity == 0.9
    finally:
        module._load.cache_clear()


# ------------------------------------------------------------------ the LLM hand over


def ambiguous_decision(n: int) -> tuple[list, dict, dict]:
    from app.mapping.decide import Decision

    decisions, profiles = [], {}
    for i in range(n):
        d = Decision("T", f"c{i}", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.6), ("x.b", 0.58), ("x.c", 0.5)])
        decisions.append(d)
        profiles[d.key] = profile(["Rock", "Jazz", "Metal"] * 20, f"c{i}")
    info = {k: {"type": "text", "description": f"Description of {k}."} for k in ("x.a", "x.b", "x.c")}
    return decisions, profiles, info


def test_requests_are_batched_by_twelve_and_stay_under_the_token_cap() -> None:
    decisions, profiles, info = ambiguous_decision(30)
    payloads = [llm_hook.build_payload(d, profiles[d.key], info) for d in decisions]
    requests = llm_hook.build_requests(payloads)
    assert [len(r) for r in requests] == [12, 12, 6]
    assert all(llm_hook.estimate_tokens(r) <= llm_hook.MAX_REQUEST_TOKENS for r in requests)
    big = [{**payloads[0], "id": f"c{i}", "candidates": payloads[0]["candidates"] * 40} for i in range(10)]
    assert all(llm_hook.estimate_tokens(r) <= llm_hook.MAX_REQUEST_TOKENS or len(r) == 1 for r in llm_hook.build_requests(big))


def test_one_call_per_batch_not_per_column() -> None:
    decisions, profiles, info = ambiguous_decision(25)
    calls: list[int] = []

    def resolver(request: list[dict]) -> dict:
        calls.append(len(request))
        return {item["id"]: {"target": "x.b", "confidence": 0.9} for item in request}

    requests, tokens = llm_hook.resolve(decisions, profiles, info, resolver, MappingThresholds())
    assert calls == [12, 12, 1] and requests == 3 and tokens > 0
    assert all(d.status == AUTO and d.method == "llm" and d.target == "x.b" for d in decisions)


def test_llm_answers_are_validated() -> None:
    decisions, profiles, info = ambiguous_decision(5)
    answers = {
        "T.c0": {"target": "x.c", "confidence": 0.95, "hint": "parse_date"},
        "T.c1": {"target": "x.b", "confidence": 0.4},  # not confident
        "T.c2": {"target": "invented.field", "confidence": 0.99},  # not offered
        "T.c3": {"target": None, "confidence": 0.9},  # no match
        # T.c4 is missing
    }
    llm_hook.resolve(decisions, profiles, info, lambda request: answers, MappingThresholds())
    status = {d.column: (d.status, d.target) for d in decisions}
    assert status == {"c0": (AUTO, "x.c"), "c1": (REVIEW, "x.b"), "c2": (REVIEW, "x.a"), "c3": (UNMAPPED, None), "c4": (REVIEW, "x.a")}
    assert "transform hint: parse_date" in decisions[0].reason


def test_llm_payloads_contain_metadata_only() -> None:
    from app.mapping.decide import Decision

    emails = [f"user{i}@example.com" for i in range(40)]
    p = profile(emails, "email_adr")
    d = Decision("T", "email_adr", NEEDS_LLM, "x.a", 0.6, 0.0, 0.5, "embedding", [("x.a", 0.6)])
    payload = json.dumps(llm_hook.build_payload(d, p, {"x.a": {"type": "text", "description": "d"}}))
    assert "example.com" not in payload and "user1" not in payload and '"pii":true' in payload.replace(" ", "")  # the pattern class stays
    assert set(json.loads(payload)["column"]) == {"name", "type", "semantic_type", "pattern_class", "pii", "samples"}


def test_nothing_is_sent_when_nothing_is_ambiguous() -> None:
    decisions, profiles, info = ambiguous_decision(3)
    for d in decisions:
        d.status = AUTO
    assert llm_hook.resolve(decisions, profiles, info, lambda request: pytest.fail("called"), MappingThresholds()) == (0, 0)


# ------------------------------------------------------------------ embedding service


@pytest.fixture(scope="module")
def service() -> EmbeddingService:
    svc = get_embedding_service()
    try:
        svc.encode(["warm up"])
    except EmbeddingUnavailable as exc:  # pragma: no cover
        pytest.skip(str(exc))
    return svc


def test_vectors_are_384_dimensional_and_normalised(service: EmbeddingService) -> None:
    vectors = service.encode(["customer last name", "surname of the customer", "invoice total"])
    assert vectors.shape == (3, DIMENSIONS) and vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)
    assert float(vectors[0] @ vectors[1]) > float(vectors[0] @ vectors[2])  # meaning, not spelling
    assert service.encode([]).shape == (0, DIMENSIONS)


def test_encoding_is_batched_and_deterministic(service: EmbeddingService) -> None:
    items = [f"column number {i}" for i in range(100)]
    first = service.encode(items)
    again = service.encode(items[::-1])[::-1]
    assert first.shape == (100, DIMENSIONS) and np.allclose(first, again, atol=1e-5)
    assert service.batch_size == 32


def test_the_model_is_loaded_once_per_process(service: EmbeddingService) -> None:
    for _ in range(5):
        service.encode(["another batch"])
    assert service.loads == 1 and get_embedding_service() is service
    fresh = EmbeddingService(model_name="no-such/model-xyz", cache_dir="./var/models")
    with pytest.raises(EmbeddingUnavailable):
        fresh.encode(["x"])
