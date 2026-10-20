"""Phase 4: discovery through the API, persisted evidence and tenant isolation."""

import json
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models.enums import JobStatus
from app.discovery import service
from app.discovery.rea_adapter import ReaRunner
from app.db.session import tenant_session
from tests.conftest import login_as

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "datasets" / "fixtures"
REA_INSTALLED = (ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
pytestmark = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")


def _upload(client, path: Path, name: str | None = None) -> str:  # type: ignore[no-untyped-def]
    response = client.post("/api/v1/sources/upload", files={"file": (name or path.name, path.read_bytes())})
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.fixture
def commerce_job(client):  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    source_id = _upload(client, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    response = client.post(f"/api/v1/sources/{source_id}/discover", json={})
    assert response.status_code == 201, response.text
    return response.json()


def test_sqlite_discovery_through_the_api(client, commerce_job) -> None:  # type: ignore[no-untyped-def]
    assert commerce_job["status"] == "DISCOVERING" and commerce_job["kind"] == "sqlite" and commerce_job["confidence"] == 1.0
    assert commerce_job["evidence_count"] > 30
    result = client.get(f"/api/v1/jobs/{commerce_job['job_id']}/discovery")
    assert result.status_code == 200
    schema = result.json()
    assert {t["name"] for t in schema["tables"]} == {"CUST_MST", "PROD_MST", "INV_HDR", "INV_DTL", "SYS_LOG"}
    assert schema["triggers"][0]["name"] == "TRG_INV_LOG"


def test_evidence_is_viewable_per_column(client, commerce_job) -> None:  # type: ignore[no-untyped-def]
    response = client.get(f"/api/v1/jobs/{commerce_job['job_id']}/discovery/evidence", params={"subject": "CUST_MST.f_nm"})
    assert response.status_code == 200
    [evidence] = response.json()
    assert evidence["command"] == "inspect-sqlite-database" and evidence["rea_version"] == "6.7.0"
    assert evidence["raw_json"]["column"]["declared_type"] == "TEXT" and len(evidence["raw_json"]["sample"]) == 5
    assert client.get(f"/api/v1/jobs/{commerce_job['job_id']}/discovery/evidence", params={"subject": "CUST_MST.nope"}).status_code == 404
    raw = client.get(f"/api/v1/jobs/{commerce_job['job_id']}/discovery/evidence", params={"subject": "command:1:inspect-sqlite-database"})
    assert raw.status_code == 400  # raw command output is stored, but not served by this endpoint


def test_discovery_rows_are_stored_with_confidence_and_raw_evidence(client, commerce_job, admin_engine) -> None:  # type: ignore[no-untyped-def]
    with Session(admin_engine) as session:
        columns = session.scalars(select(models.SourceColumn).where(models.SourceColumn.job_id == commerce_job["job_id"])).all()
        assert len(columns) == 12 + 7 + 8 + 5 + 2
        assert {float(c.discovery_confidence) for c in columns} == {1.0}
        evidence = session.scalars(select(models.DiscoveryEvidence).where(models.DiscoveryEvidence.job_id == commerce_job["job_id"])).all()
        commands = [e for e in evidence if e.subject.startswith("command:")]
        assert len(commands) == 6 and all(e.rea_version == "6.7.0" and e.duration_ms is not None for e in commands)
        assert all("normalized_result" in e.raw_json for e in commands)
        job = session.get(models.Job, commerce_job["job_id"])
        assert job.status is JobStatus.DISCOVERING and job.started_at is not None


def test_binary_discovery_through_the_api(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    assembly = _upload(client, FIXTURES / "clinic" / "ClinicDesk.dll")
    data = _upload(client, FIXTURES / "clinic" / "clinic.cdb")
    response = client.post(f"/api/v1/sources/{assembly}/discover", json={"data_source_id": data})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "dotnet_binary" and body["method"] == "cil" and body["validated"] is True and body["confidence"] >= 0.9

    layout = client.get(f"/api/v1/jobs/{body['job_id']}/discovery").json()
    assert [t["name"] for t in layout["tables"]] == ["PATIENT", "ENCNTR", "COND", "MEDS", "OBS"]
    assert layout["validation"]["passed"] is True

    evidence = client.get(f"/api/v1/jobs/{body['job_id']}/discovery/evidence", params={"subject": "PATIENT.LastNm"}).json()
    [row] = evidence
    assert row["rea_version"] == "6.7.0" and row["command"] == "inspect-managed-members"
    payload = row["raw_json"]
    assert payload["il_offset"] > 0 and payload["call"] == "System.IO.BinaryWriter::Write" and payload["field"]["length"] == 30
    assert payload["source_hints"] == ["LAST"] and payload["declared"]["name"] == "LastNm"


def test_data_file_alone_is_analysed_statistically(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    data = _upload(client, FIXTURES / "clinic" / "clinic.cdb")
    body = client.post(f"/api/v1/sources/{data}/discover", json={}).json()
    assert body["method"] == "statistical" and body["validated"] is False and body["confidence"] <= 0.6


def test_rerunning_discovery_replaces_the_earlier_results(client, commerce_job, app_factory, api_users) -> None:  # type: ignore[no-untyped-def]
    job_id = commerce_job["job_id"]
    with tenant_session(api_users.org_id, app_factory) as session:
        job = session.get(models.Job, job_id)
        outcome = service.run_discovery(session, job)
    assert outcome.evidence_count == commerce_job["evidence_count"]
    with tenant_session(api_users.org_id, app_factory) as session:
        count = session.query(models.DiscoveryEvidence).filter_by(job_id=job_id).count()
        assert count == commerce_job["evidence_count"]  # replaced, not appended


def test_roles_and_validation(client) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    csv = client.post("/api/v1/sources/upload", files={"file": ("export.csv", b"a,b\n1,2\n")}).json()["id"]
    assert client.post(f"/api/v1/sources/{csv}/discover", json={}).status_code == 422  # profiled directly, no REA
    sqlite_id = _upload(client, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    other = _upload(client, FIXTURES / "clinic" / "clinic.cdb")
    assert client.post(f"/api/v1/sources/{sqlite_id}/discover", json={"data_source_id": other}).status_code == 422
    assert client.post("/api/v1/sources/00000000-0000-0000-0000-000000000000/discover", json={}).status_code == 404
    client.cookies.clear()
    login_as(client, "viewer@acme.test")
    assert client.post(f"/api/v1/sources/{sqlite_id}/discover", json={}).status_code == 403


def test_other_organizations_cannot_see_or_start_discovery(client, commerce_job) -> None:  # type: ignore[no-untyped-def]
    source_id = client.get("/api/v1/sources").json()[0]["id"]
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    assert client.get(f"/api/v1/jobs/{commerce_job['job_id']}/discovery").status_code == 404
    assert client.get(f"/api/v1/jobs/{commerce_job['job_id']}/discovery/evidence", params={"subject": "CUST_MST.f_nm"}).status_code == 404
    assert client.post(f"/api/v1/sources/{source_id}/discover", json={}).status_code == 404


def test_a_failed_analysis_marks_the_job_failed(client, admin_engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    source_id = _upload(client, FIXTURES / "commerce" / "commerce_legacy.sqlite")
    broken = ReaRunner([sys.executable, "-c", "import sys; sys.exit(3)", "--"], timeout_seconds=5)
    monkeypatch.setattr(service, "get_rea_runner", lambda: broken)
    response = client.post(f"/api/v1/sources/{source_id}/discover", json={})
    assert response.status_code == 502 and "runner_failed" in response.json()["detail"]
    with Session(admin_engine) as session:
        job = session.scalars(select(models.Job).where(models.Job.source_id == source_id)).one()
        assert job.status is JobStatus.FAILED and "exited" in (job.error_message or "")
        events = session.scalars(select(models.JobEvent).where(models.JobEvent.job_id == job.id).order_by(models.JobEvent.id)).all()
        assert [e.to_status for e in events] == [JobStatus.UPLOADED, JobStatus.DISCOVERING, JobStatus.FAILED]


def test_discovery_is_audited(client, commerce_job) -> None:  # type: ignore[no-untyped-def]
    client.cookies.clear()
    login_as(client, "admin@acme.test")
    entries = client.get("/api/v1/admin/audit-log", params={"action": "discovery.run"}).json()
    assert commerce_job["job_id"] in {e["entity_id"] for e in entries}
    assert json.dumps(entries)  # serialisable
