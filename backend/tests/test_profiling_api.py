"""Phase 5: profiling through the API, persisted results, privacy of stored profiles and tenant isolation."""

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.enums import JobStatus
from app.evaluation.quality_score import aliases_from_layout, score_quality
from app.discovery.models import LayoutSpec
from app.profiling import service as profiling_service
from app.profiling.models import QualityReport
from tests.conftest import login_as

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "datasets" / "fixtures"
REA_INSTALLED = (ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
needs_rea = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")


def _upload(client, path: Path, name: str | None = None, content: bytes | None = None) -> str:  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/sources/upload", files={"file": (name or path.name, content if content is not None else path.read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def commerce_profiled(client):  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    source = _upload(client, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    job = client.post(f"/api/v1/sources/{source}/discover", json={}).json()["job_id"]
    response = client.post(f"/api/v1/jobs/{job}/profile")
    assert response.status_code == 200, response.text
    return {"job_id": job, "summary": response.json()}


@needs_rea
def test_sqlite_profile_through_the_api(client, commerce_profiled) -> None:  # type: ignore[no-untyped-def]
    summary, job = commerce_profiled["summary"], commerce_profiled["job_id"]
    assert summary["status"] == "PROFILED" and summary["tables"] == 5 and summary["rows"] == 60 + 3573 + 420 + 2285
    assert summary["pii_columns"] >= 8 and 80 < summary["quality_score"] < 100
    assert {"duplicate", "required_null", "invalid_date", "out_of_range", "orphan_fk"} <= set(summary["issue_counts"])

    profile = client.get(f"/api/v1/jobs/{job}/profile").json()
    assert profile["source_kind"] == "sqlite" and len(profile["tables"]) == 5
    email = next(c for t in profile["tables"] if t["name"] == "CUST_MST" for c in t["columns"] if c["name"] == "email_adr")
    assert email["semantic_type"] == "email" and email["pii"] is True and email["top_values"] == [] and email["min"] is None

    quality = client.get(f"/api/v1/jobs/{job}/quality", params={"type": "orphan_fk", "limit": 5}).json()
    assert quality["issues_total"] == quality["issue_counts"]["orphan_fk"] and len(quality["issues"]) == 5
    assert all(i["type"] == "orphan_fk" for i in quality["issues"]) and "0.25" in quality["formula"]
    page2 = client.get(f"/api/v1/jobs/{job}/quality", params={"type": "orphan_fk", "limit": 5, "offset": 5}).json()["issues"]
    assert page2 and page2[0] != quality["issues"][0]
    only_table = client.get(f"/api/v1/jobs/{job}/quality", params={"table": "INV_HDR", "limit": 1000}).json()
    assert {i["table"] for i in only_table["issues"]} == {"INV_HDR"}


@needs_rea
def test_profile_results_are_stored_on_the_source_columns(client, commerce_profiled, admin_engine) -> None:  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        cols = {(c.table_name, c.column_name): c for c in session.scalars(select(models.SourceColumn).where(models.SourceColumn.job_id == commerce_profiled["job_id"]))}
        assert len(cols) == 34
        email = cols[("CUST_MST", "email_adr")]
        assert email.pii and email.semantic_type == "email" and float(email.null_rate) > 0.0 and email.distinct_count >= 50
        assert email.profile["stats"]["pii_kinds"] == ["email"] and "ordinal" in email.profile  # discovery facts are kept
        assert cols[("INV_HDR", "inv_dt")].inferred_type == "date" and not cols[("INV_HDR", "inv_dt")].pii
        assert cols[("INV_HDR", "cust_no")].profile["stats"]["references"] == "CUST_MST.cust_no"
        assert cols[("CUST_MST", "f_nm")].normalized_name == "first name"
        job = session.get(models.Job, commerce_profiled["job_id"])
        assert job.status is JobStatus.PROFILED
        events = session.scalars(select(models.JobEvent).where(models.JobEvent.job_id == job.id).order_by(models.JobEvent.id)).all()
        assert [e.to_status for e in events] == [JobStatus.UPLOADED, JobStatus.DISCOVERING, JobStatus.PROFILED]
        assert events[-1].detail["quality_score"] > 80


@needs_rea
def test_no_pii_value_is_stored_anywhere(client, commerce_profiled, admin_engine) -> None:  # type: ignore[no-untyped-def]
    secrets = ["luisg@embraer.com.br", "+55 (12) 3923-5555", "Theodor-Heuss-Stra"]
    with Session(admin_engine) as session:
        for row in session.scalars(select(models.SourceColumn).where(models.SourceColumn.job_id == commerce_profiled["job_id"], models.SourceColumn.pii.is_(True))):
            text = json.dumps(row.profile)
            assert not any(s in text for s in secrets), (row.table_name, row.column_name)
        stored = session.scalars(select(models.DiscoveryEvidence).where(
            models.DiscoveryEvidence.job_id == commerce_profiled["job_id"], models.DiscoveryEvidence.subject.in_(["result:profile", "result:quality"])))
        for evidence in stored:
            text = json.dumps(evidence.raw_json)
            assert "luisg@embraer" not in text and "3923-5555" not in text
        quality = next(e for e in session.scalars(select(models.DiscoveryEvidence).where(
            models.DiscoveryEvidence.job_id == commerce_profiled["job_id"], models.DiscoveryEvidence.subject == "result:quality")))
        assert all(i["value"] is None for i in quality.raw_json["issues"] if i["column"] in {"email_adr", "l_nm", "f_nm", "ph_no", "zip_cd"})


@needs_rea
def test_binary_source_end_to_end_meets_the_recall_target(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    assembly = _upload(client, FIXTURES / "clinic" / "ClinicDesk.dll")
    data = _upload(client, FIXTURES / "clinic" / "clinic.cdb")
    discovered = client.post(f"/api/v1/sources/{assembly}/discover", json={"data_source_id": data}).json()
    job = discovered["job_id"]
    summary = client.post(f"/api/v1/jobs/{job}/profile")
    assert summary.status_code == 200, summary.text
    assert summary.json()["status"] == "PROFILED" and summary.json()["tables"] == 5 and summary.json()["rows"] == 306 + 7855 + 5391 + 6228 + 6120

    layout = LayoutSpec.model_validate(client.get(f"/api/v1/jobs/{job}/discovery").json())
    quality = client.get(f"/api/v1/jobs/{job}/quality", params={"limit": 1000}).json()
    full = []
    for offset in range(0, quality["issues_total"], 1000):
        full += client.get(f"/api/v1/jobs/{job}/quality", params={"limit": 1000, "offset": offset}).json()["issues"]
    report = QualityReport(score=quality["score"], formula=quality["formula"], tables=quality["tables"], issue_counts=quality["issue_counts"], issues=full)
    truth = json.loads((FIXTURES / "clinic" / "ground_truth.json").read_text(encoding="utf-8"))
    names = {t["entity"]: t["name"] for t in truth["layout"]["tables"]}
    score = score_quality(report, truth["defects"], table_names=names, column_aliases=aliases_from_layout(layout))
    assert score.recall >= 0.9 and score.core_recall >= 0.99
    profile = client.get(f"/api/v1/jobs/{job}/profile").json()
    patient = next(t for t in profile["tables"] if t["name"] == "PATIENT")
    assert {c["name"] for c in patient["columns"] if c["pii"]} == {"LastNm", "FirstNm", "BirthDt", "DeathDt", "Ssn", "Addr1", "ZipCd"}


def test_text_files_are_profiled_directly(client, admin_engine) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    lines = ["id,email,amount,joined"] + [f"{i},user{i}@example.com,{i % 50 + 0.5:.2f},2021-0{i % 9 + 1}-1{i % 9}" for i in range(1, 81)]
    lines += ["81,,9999999.00,not a date"]
    source = _upload(client, FIXTURES, "customers.csv", content=("\n".join(lines) + "\n").encode())
    response = client.post(f"/api/v1/sources/{source}/profile")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PROFILED" and body["tables"] == 1 and body["rows"] == 81 and body["pii_columns"] == 1
    assert {"required_null", "invalid_date", "out_of_range"} <= set(body["issue_counts"])
    with Session(admin_engine) as session:
        job = session.get(models.Job, body["job_id"])
        events = session.scalars(select(models.JobEvent).where(models.JobEvent.job_id == job.id).order_by(models.JobEvent.id)).all()
        assert [e.to_status for e in events] == [JobStatus.UPLOADED, JobStatus.DISCOVERING, JobStatus.PROFILED]
        columns = session.scalars(select(models.SourceColumn).where(models.SourceColumn.job_id == job.id)).all()
        assert {c.column_name: c.semantic_type for c in columns}["email"] == "email"


def test_profiling_needs_discovery_first_for_databases_and_binaries(client, admin_engine, api_users) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    data = _upload(client, FIXTURES / "clinic" / "clinic.cdb")
    assert client.post(f"/api/v1/sources/{data}/profile").status_code == 422  # not a text file
    with Session(admin_engine) as session:
        job = models.Job(org_id=api_users.org_id, source_id=data, status=JobStatus.UPLOADED)
        session.add(job)
        session.commit()
        job_id = str(job.id)
    response = client.post(f"/api/v1/jobs/{job_id}/profile")
    assert response.status_code == 409 and "discovery" in response.json()["detail"].lower()
    assert client.post("/api/v1/jobs/00000000-0000-0000-0000-000000000000/profile").status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}/profile").status_code == 404 and client.get(f"/api/v1/jobs/{job_id}/quality").status_code == 404


@needs_rea
def test_rerunning_replaces_results_and_is_audited(client, commerce_profiled, admin_engine) -> None:  # type: ignore[no-untyped-def]
    job = commerce_profiled["job_id"]
    again = client.post(f"/api/v1/jobs/{job}/profile")
    assert again.status_code == 200 and again.json()["quality_score"] == commerce_profiled["summary"]["quality_score"]
    with Session(admin_engine) as session:
        subjects = session.scalars(select(models.DiscoveryEvidence.subject).where(models.DiscoveryEvidence.job_id == job, models.DiscoveryEvidence.subject.like("result:%"))).all()
        assert sorted(subjects) == ["result:profile", "result:quality", "result:schema"]
    client.cookies.clear()
    login_as(client, "admin@acme.test")
    entries = client.get("/api/v1/admin/audit-log", params={"action": "profile.run"}).json()
    assert len([e for e in entries if e["entity_id"] == job]) == 2


@needs_rea
def test_roles_and_tenant_isolation(client, commerce_profiled) -> None:  # type: ignore[no-untyped-def]
    job = commerce_profiled["job_id"]
    client.cookies.clear()
    login_as(client, "viewer@acme.test")
    assert client.get(f"/api/v1/jobs/{job}/profile").status_code == 200 and client.get(f"/api/v1/jobs/{job}/quality").status_code == 200
    assert client.post(f"/api/v1/jobs/{job}/profile").status_code == 403
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    assert client.get(f"/api/v1/jobs/{job}/profile").status_code == 404
    assert client.get(f"/api/v1/jobs/{job}/quality").status_code == 404
    assert client.post(f"/api/v1/jobs/{job}/profile").status_code == 404


@needs_rea
def test_a_crash_while_profiling_fails_the_job(client, commerce_profiled, admin_engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def broken(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("disk error")

    monkeypatch.setattr(profiling_service, "profile_and_check", broken)
    response = client.post(f"/api/v1/jobs/{commerce_profiled['job_id']}/profile")
    assert response.status_code == 500 and "disk error" not in response.text  # internals are not exposed
    with Session(admin_engine) as session:
        assert session.get(models.Job, commerce_profiled["job_id"]).status is JobStatus.FAILED
