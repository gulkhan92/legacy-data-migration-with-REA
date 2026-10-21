"""Phase 6 acceptance on both sample systems: how many columns are mapped correctly without and with the LLM step.

Uses the real pipeline: profiles of the fixtures (the clinic layout comes from REA discovery), the bge embedding model,
the tuned thresholds. The LLM step is exercised with a deterministic stand-in resolver (the router and real providers come
with Phase 7), so the "after the LLM step" figure shows the plumbing and the ceiling, not the quality of a real model.
"""

import shutil

import pytest

from app.evaluation.mapping_inputs import describe_targets, scenario, truth_columns
from app.evaluation.mapping_score import MappingScore, score_decisions
from app.mapping import compat, llm_hook
from app.mapping.decide import AUTO, MEMORY, NEEDS_LLM, UNMAPPED, decide
from app.mapping.embedding import EmbeddingUnavailable, get_embedding_service
from app.mapping.thresholds import MappingThresholds, load_thresholds
from app.discovery.rea_adapter import REPO_ROOT
from app.profiling.vocabulary import expanded_words

REA_INSTALLED = (REPO_ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
pytestmark = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")

EXPECTED_TABLE = {
    "CUST_MST": "customers", "PROD_MST": "products", "INV_HDR": "orders", "INV_DTL": "order_items",
    "PATIENT": "patients", "ENCNTR": "encounters", "COND": "conditions", "MEDS": "medications", "OBS": "observations",
}  # fmt: skip


@pytest.fixture(scope="module", autouse=True)
def _model() -> None:
    try:
        get_embedding_service().encode(["warm up"])
    except EmbeddingUnavailable as exc:  # pragma: no cover
        pytest.skip(str(exc))


def run(name: str, t: MappingThresholds | None = None, tables: set[str] | None = None):  # type: ignore[no-untyped-def]
    s = scenario(name)
    return s, decide(truth_columns(s, tables), s.targets, t or load_thresholds())


def both(t: MappingThresholds | None = None, tables: dict[str, set[str] | None] | None = None):  # type: ignore[no-untyped-def]
    out, truth = [], {}
    for name in ("commerce", "clinic"):
        s, decisions = run(name, t, (tables or {}).get(name))
        out += decisions
        truth.update(s.truth)
    return out, truth


def lexical_stand_in(target_info):  # type: ignore[no-untyped-def]
    """Picks the candidate that shares the most words with the column. Deterministic and not tuned on the answers."""

    def resolver(request: list[dict]) -> dict:
        answers = {}
        for item in request:
            words = set(expanded_words(item["column"]["name"].replace(" ", "_"))) | set(item["column"]["name"].split())
            best, best_overlap = None, 0
            for candidate in item["candidates"]:
                target_words = set(candidate["key"].replace(".", " ").replace("_", " ").split()) | set(expanded_words(candidate["description"].replace(" ", "_")))
                overlap = len(words & target_words)
                if overlap > best_overlap:
                    best, best_overlap = candidate["key"], overlap
            answers[item["id"]] = {"target": best, "confidence": 0.9 if best else 0.8}
        return answers

    return resolver


# ------------------------------------------------------------------ acceptance


def test_at_least_85_percent_of_columns_are_mapped_correctly_without_any_llm_call() -> None:
    decisions, truth = both()
    score = score_decisions(decisions, truth)
    assert score.total == 76
    assert score.accuracy >= 0.85, (score.accuracy, score.wrong, score.by_method)
    assert MEMORY not in score.by_method  # a first run has nothing in memory
    assert score.wrong_settled <= 4, score.wrong


def test_each_dataset_alone_meets_the_target() -> None:
    for name in ("commerce", "clinic"):
        s, decisions = run(name)
        score = score_decisions(decisions, s.truth)
        assert score.accuracy >= 0.85, (name, score.wrong, score.by_method)


def test_the_held_out_tables_meet_the_target() -> None:
    """The clinic tables the thresholds were never tuned on."""
    s, decisions = run("clinic", tables={"COND", "MEDS", "OBS"})
    score = score_decisions(decisions, s.truth)
    assert score.total == 23 and score.accuracy >= 0.85, (score.wrong, score.by_method)
    tuning = load_thresholds().tuning
    assert tuning["held_out"]["accuracy"] >= 0.85 and tuning["tuning"]["accuracy"] >= 0.85


def test_at_least_95_percent_are_reachable_after_the_llm_step() -> None:
    decisions, truth = both()
    score = score_decisions(decisions, truth)
    assert score.ceiling >= 0.95, (score.ceiling, score.unreachable, score.wrong)


def test_after_the_llm_step_with_a_stand_in_resolver() -> None:
    """Columns the rules could not settle go to one batched call each 12; the stand in is deliberately simple."""
    t = load_thresholds()
    total = MappingScore()
    calls = 0
    for name in ("commerce", "clinic"):
        s = scenario(name)
        columns = truth_columns(s)
        decisions = decide(columns, s.targets, t)
        waiting_before = sum(1 for d in decisions if d.status == NEEDS_LLM)
        info = describe_targets(s.target_fields)
        profiles = {c.key: c.profile for c in columns}
        requests, tokens = llm_hook.resolve(decisions, profiles, info, lexical_stand_in(info), t)
        calls += requests
        assert requests == (0 if waiting_before == 0 else -(-waiting_before // 12)) and tokens < 5000 * max(requests, 1)
        part = score_decisions(decisions, s.truth)
        total.total, total.correct, total.wrong_settled = total.total + part.total, total.correct + part.correct, total.wrong_settled + part.wrong_settled
    assert calls <= 2  # at most one batched call per dataset
    assert total.correct / total.total >= 0.90  # the stand in only has to avoid making things worse


# ------------------------------------------------------------------ properties of the decisions


def test_every_accepted_mapping_passes_the_type_gate() -> None:
    for name in ("commerce", "clinic"):
        s, decisions = run(name)
        profile_of = {c.key: c.profile for c in s.columns}
        fields = {tg.key: tg for tg in s.targets}
        for d in decisions:
            if d.target:
                assert compat.compatible(profile_of[d.key], fields[d.target].type), (d.key, d.target)


def test_source_tables_map_to_the_right_target_tables() -> None:
    for name in ("commerce", "clinic"):
        _, decisions = run(name)
        by_table: dict[str, set[str]] = {}
        for d in decisions:
            if d.target_table:
                by_table.setdefault(d.table, set()).add(d.target_table)
        for table, targets in by_table.items():
            assert targets == {EXPECTED_TABLE[table]}, (table, targets)


def test_no_target_field_is_used_twice() -> None:
    for name in ("commerce", "clinic"):
        _, decisions = run(name)
        used = [d.target for d in decisions if d.target and d.status in (AUTO, MEMORY)]
        assert len(used) == len(set(used))


def test_columns_without_a_counterpart_are_left_unmapped() -> None:
    s, decisions = run("commerce")
    by_key = {d.key: d for d in decisions}
    for key in ("CUST_MST.fax_no", "PROD_MST.len_ms", "PROD_MST.sz_bytes", "INV_HDR.bill_addr1"):
        assert by_key[key].status == UNMAPPED and by_key[key].target is None, key
    assert by_key["CUST_MST.cust_no"].target == "customers.source_key" and by_key["INV_HDR.cust_no"].target == "orders.customer_id"
    assert by_key["INV_DTL.inv_no"].target == "order_items.order_id" and by_key["INV_HDR.inv_no"].target == "orders.source_key"


def test_the_thresholds_in_the_config_file_drive_the_result() -> None:
    strict = load_thresholds().model_copy(update={"reject_similarity": 0.99, "auto_similarity": 0.995})
    _, decisions = run("commerce", strict)
    assert all(d.status == UNMAPPED for d in decisions)
    lax = load_thresholds().model_copy(update={"auto_similarity": 0.99})
    _, decisions = run("commerce", lax)
    assert sum(1 for d in decisions if d.status == NEEDS_LLM) > 10


def test_pii_columns_are_embedded_without_their_values() -> None:
    from app.mapping import texts

    for name in ("commerce", "clinic"):
        for c in scenario(name).columns:
            if c.profile.pii:
                text = texts.source_text(c.table, c.profile)
                assert "examples" not in text and not c.profile.top_values
