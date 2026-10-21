"""Phase 6: seeded target schemas, mapping through the API, pgvector retrieval and mapping memory."""

import copy
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.enums import JobStatus, MappingMethod, MappingStatus
from app.db.session import tenant_session
from app.evaluation.mapping_score import clinic_truth, commerce_truth, score_decisions
from app.mapping import service
from app.mapping.decide import Decision
from app.mapping.embedding import DIMENSIONS, EmbeddingUnavailable, get_embedding_service
from app.mapping.seed_targets import load_schema_files, seed
from tests.conftest import login_as

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "datasets" / "fixtures"
REA_INSTALLED = (ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
needs_rea = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")


@pytest.fixture(scope="session")
def seeded(admin_engine):  # type: ignore[no-untyped-def]
    try:
        get_embedding_service().encode(["warm up"])
    except EmbeddingUnavailable as exc:  # pragma: no cover
        pytest.skip(str(exc))
    with Session(admin_engine) as session:
        return seed(session)


def _upload(client, path: Path, name: str | None = None) -> str:  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/sources/upload", files={"file": (name or path.name, path.read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def profiled_commerce_job(client) -> str:  # type: ignore[no-untyped-def]
    source = _upload(client, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    job = client.post(f"/api/v1/sources/{source}/discover", json={}).json()["job_id"]
    assert client.post(f"/api/v1/jobs/{job}/profile").status_code == 200
    return job


def profiled_clinic_job(client) -> str:  # type: ignore[no-untyped-def]
    assembly = _upload(client, FIXTURES / "clinic" / "ClinicDesk.dll")
    data = _upload(client, FIXTURES / "clinic" / "clinic.cdb")
    job = client.post(f"/api/v1/sources/{assembly}/discover", json={"data_source_id": data}).json()["job_id"]
    assert client.post(f"/api/v1/jobs/{job}/profile").status_code == 200
    return job


def as_decisions(rows: list[dict]) -> list[Decision]:
    return [
        Decision(r["source_table"], r["source_column"], r["decision"], r["target"], r["similarity"], r["margin"], r["confidence"], r["method"],
                 [(c["field"], c["similarity"]) for c in r["candidates"]])  # fmt: skip
        for r in rows
    ]


def truth_of(kind: str) -> dict:
    ground = json.loads((FIXTURES / kind / "ground_truth.json").read_text(encoding="utf-8"))
    return commerce_truth(ground) if kind == "commerce" else clinic_truth(ground)


# ------------------------------------------------------------------ target schemas


def test_target_schemas_are_embedded_in_pgvector(seeded, admin_engine) -> None:  # type: ignore[no-untyped-def]
    assert seeded == {"clinic": 42, "commerce": 27}
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT s.name, count(*), count(f.embedding), min(vector_dims(f.embedding)), max(vector_dims(f.embedding)) "
                 "FROM target_fields f JOIN target_schemas s ON s.id = f.schema_id GROUP BY s.name ORDER BY s.name")
        ).all()
    assert [tuple(r) for r in rows] == [("clinic", 42, 42, DIMENSIONS, DIMENSIONS), ("commerce", 27, 27, DIMENSIONS, DIMENSIONS)]
    with admin_engine.connect() as conn:
        norms = conn.execute(text("SELECT min(vector_norm(embedding)), max(vector_norm(embedding)) FROM target_fields")).one()
    assert 0.999 < norms[0] and norms[1] < 1.001  # normalised vectors


def test_seeding_twice_changes_nothing_and_a_changed_description_is_re_embedded(seeded, admin_engine) -> None:  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        before = {(f.table_name, f.name): (f.id, list(f.embedding)) for f in session.scalars(select(models.TargetField))}
        seed(session)
        after = {(f.table_name, f.name): (f.id, list(f.embedding)) for f in session.scalars(select(models.TargetField))}
        assert {k: v[0] for k, v in before.items()} == {k: v[0] for k, v in after.items()} and len(after) == 69
        changed = copy.deepcopy(load_schema_files())
        field = next(f for s in changed if s["schema"] == "commerce" for f in s["fields"] if f["name"] == "company")
        field["description"] = "Legal name of the firm that employs the buyer."
        seed(session, changed)
        row = session.scalar(select(models.TargetField).where(models.TargetField.name == "company"))
        assert row.description.startswith("Legal name") and list(row.embedding) != before[("customers", "company")][1]
        seed(session)  # restore


def test_target_schema_routes(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "viewer@acme.test")
    schemas = {s["name"]: s for s in client.get("/api/v1/target-schemas").json()}
    assert {"clinic", "commerce"} <= set(schemas) and schemas["commerce"]["built_in"] is True
    fields = client.get(f"/api/v1/target-schemas/{schemas['commerce']['id']}/fields").json()
    assert len(fields) == 27 and {f["table"] for f in fields} == {"customers", "products", "orders", "order_items"}
    assert all("embedding" not in f for f in fields)
    assert client.get("/api/v1/target-schemas/00000000-0000-0000-0000-000000000000/fields").status_code == 404


def test_built_in_schemas_are_visible_to_every_organization(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "admin@globex.test")
    assert {"clinic", "commerce"} <= {s["name"] for s in client.get("/api/v1/target-schemas").json()}


# ------------------------------------------------------------------ mapping end to end


@needs_rea
def test_commerce_job_is_mapped_through_the_api(client, seeded, admin_engine) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    job = profiled_commerce_job(client)
    response = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "commerce"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "MAPPED" and body["columns"] == 34 and body["llm_requests"] == 0
    assert body["counts"]["auto"] >= 25 and body["counts"]["memory"] == 0

    rows = client.get(f"/api/v1/jobs/{job}/mappings").json()
    assert len(rows) == 34
    score = score_decisions(as_decisions(rows), truth_of("commerce"))
    assert score.accuracy >= 0.85, (score.wrong, score.by_method)
    by = {(r["source_table"], r["source_column"]): r for r in rows}
    row = by[("INV_HDR", "cust_no")]
    assert row["target"] == "orders.customer_id" and row["method"] == "embedding" and row["status"] == "suggested" and row["decision"] == "auto"
    assert 0.6 < row["similarity"] < 1 and 0 < row["confidence"] <= 1 and 1 <= len(row["candidates"]) <= 3
    assert row["candidates"][0]["field"] == "orders.customer_id" and row["rationale"]
    assert by[("CUST_MST", "fax_no")]["target"] is None and by[("CUST_MST", "fax_no")]["decision"] == "unmapped"
    assert by[("CUST_MST", "email_adr")]["pii"] is True

    with Session(admin_engine) as session:
        stored = session.scalars(select(models.SourceColumn).where(models.SourceColumn.job_id == job)).all()
        assert len(stored) == 34 and all(c.embedding is not None and len(c.embedding) == DIMENSIONS for c in stored)
        assert session.get(models.Job, job).target_schema_id is not None
        assert session.get(models.Job, job).status is JobStatus.MAPPED
        tokens_free = session.scalar(select(func.count()).select_from(models.LlmCall).where(models.LlmCall.job_id == job))
        assert tokens_free == 0  # no LLM call was needed
    client.cookies.clear()
    login_as(client, "admin@acme.test")
    entries = client.get("/api/v1/admin/audit-log", params={"action": "mapping.run"}).json()
    assert job in {e["entity_id"] for e in entries}


@needs_rea
def test_clinic_job_is_mapped_through_the_api(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)
    body = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "clinic"}).json()
    assert body["status"] == "MAPPED" and body["columns"] == 44
    rows = client.get(f"/api/v1/jobs/{job}/mappings").json()
    score = score_decisions(as_decisions(rows), truth_of("clinic"))
    assert score.accuracy >= 0.85, (score.wrong, score.by_method)
    by = {(r["source_table"], r["source_column"]): r for r in rows}
    assert by[("PATIENT", "LastNm")]["target"] == "patients.last_name" and by[("ENCNTR", "CostCents")]["target"] == "encounters.total_cost_cents"
    assert by[("COND", "PatNo")]["target"] == "conditions.patient_id"


@needs_rea
def test_wrong_requests_are_refused(client, seeded, admin_engine, api_users) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    source = _upload(client, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    job = client.post(f"/api/v1/sources/{source}/discover", json={}).json()["job_id"]  # discovered but not profiled
    assert client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "commerce"}).status_code == 409
    profiled = profiled_commerce_job(client)
    assert client.post(f"/api/v1/jobs/{profiled}/map", json={"target_schema": "no-such-schema"}).status_code == 409
    assert client.post(f"/api/v1/jobs/{profiled}/map", json={}).status_code == 422
    assert client.post("/api/v1/jobs/00000000-0000-0000-0000-000000000000/map", json={"target_schema": "commerce"}).status_code == 404
    assert client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000/mappings").status_code == 404
    client.cookies.clear()
    login_as(client, "viewer@acme.test")
    assert client.post(f"/api/v1/jobs/{profiled}/map", json={"target_schema": "commerce"}).status_code == 403
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    assert client.post(f"/api/v1/jobs/{profiled}/map", json={"target_schema": "commerce"}).status_code == 404
    assert client.get(f"/api/v1/jobs/{profiled}/mappings").status_code == 404


# ------------------------------------------------------------------ memory


def _analyst_id(admin_engine) -> str:  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        return str(session.scalar(select(models.User.id).where(models.User.email == "analyst@acme.test")))


@needs_rea
def test_approved_mappings_are_reused_with_zero_tokens_and_stay_inside_the_organization(client, seeded, admin_engine, app_factory, api_users) -> None:  # type: ignore[no-untyped-def]
    import uuid

    login_as(client, "analyst@acme.test")
    first = profiled_commerce_job(client)
    first_run = client.post(f"/api/v1/jobs/{first}/map", json={"target_schema": "commerce"}).json()
    assert first_run["counts"]["memory"] == 0

    with tenant_session(api_users.org_id, app_factory) as session:
        job = session.get(models.Job, first)
        approved = service.approve_decided(session, job, uuid.UUID(_analyst_id(admin_engine)))
    assert approved >= 25
    with Session(admin_engine) as session:
        memory = session.scalars(select(models.MappingMemory).where(models.MappingMemory.org_id == api_users.org_id)).all()
        assert len(memory) >= 25 and all(m.times_approved >= 1 and m.embedding is not None and "@" not in m.source_text for m in memory)
        approved_rows = session.scalars(select(models.Mapping).where(models.Mapping.job_id == first, models.Mapping.status == MappingStatus.APPROVED)).all()
        assert len(approved_rows) == approved and all(m.reviewed_by is not None and m.reviewed_at is not None for m in approved_rows)

    second = profiled_commerce_job(client)  # the same legacy system again
    again = client.post(f"/api/v1/jobs/{second}/map", json={"target_schema": "commerce"}).json()
    assert again["counts"]["memory"] >= approved - 2 and again["llm_requests"] == 0 and again["counts"]["needs_llm"] == 0
    rows = client.get(f"/api/v1/jobs/{second}/mappings").json()
    from_memory = [r for r in rows if r["decision"] == "memory"]
    assert from_memory and all(r["method"] == "memory" and r["confidence"] > 0.9 for r in from_memory)
    assert score_decisions(as_decisions(rows), truth_of("commerce")).accuracy >= first_accuracy(client, first)

    client.cookies.clear()
    login_as(client, "admin@globex.test")  # another organization has no memory of it
    other = profiled_commerce_job(client)
    other_run = client.post(f"/api/v1/jobs/{other}/map", json={"target_schema": "commerce"}).json()
    assert other_run["counts"]["memory"] == 0


def first_accuracy(client, job: str) -> float:  # type: ignore[no-untyped-def]
    return score_decisions(as_decisions(client.get(f"/api/v1/jobs/{job}/mappings").json()), truth_of("commerce")).accuracy


@needs_rea
def test_an_override_is_remembered_as_a_human_decision(client, seeded, admin_engine, app_factory, api_users) -> None:  # type: ignore[no-untyped-def]
    import uuid

    login_as(client, "analyst@acme.test")
    job = profiled_commerce_job(client)
    client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "commerce"})
    analyst = uuid.UUID(_analyst_id(admin_engine))
    with tenant_session(api_users.org_id, app_factory) as session:
        mapping = session.scalar(
            select(models.Mapping).join(models.SourceColumn, models.SourceColumn.id == models.Mapping.source_column_id)
            .where(models.Mapping.job_id == job, models.SourceColumn.column_name == "co_nm")
        )  # fmt: skip
        other_field = session.scalar(select(models.TargetField).where(models.TargetField.name == "city", models.TargetField.table_name == "customers"))
        memory = service.approve_mapping(session, mapping, analyst, target_field_id=other_field.id)
        assert mapping.status is MappingStatus.OVERRIDDEN and mapping.method is MappingMethod.HUMAN and mapping.target_field_id == other_field.id
        assert memory.target_field_id == other_field.id and memory.times_approved == 1
        again = service.approve_mapping(session, mapping, analyst)
        assert again.id == memory.id and again.times_approved == 2  # one row per signature, counting approvals
        assert mapping.status is MappingStatus.APPROVED
