import uuid

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.deps as deps
from app.api.deps import get_tenant_db
from app.db import models


@pytest.fixture
def client(app_factory, monkeypatch) -> TestClient:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(deps, "make_session_factory", lambda: app_factory)
    api = FastAPI()

    @api.middleware("http")
    async def fake_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Stand in for the Phase 2 authentication layer.
        header = request.headers.get("x-test-org")
        if header:
            request.state.org_id = uuid.UUID(header)
        return await call_next(request)

    @api.get("/jobs")
    def list_jobs(db: Session = Depends(get_tenant_db)) -> list[str]:
        return [str(j) for j in db.scalars(select(models.Job.id))]

    @api.get("/org")
    def org(db: Session = Depends(get_tenant_db)) -> list[str]:
        return [str(o) for o in db.scalars(select(models.Organization.id))]

    return TestClient(api)


def test_request_without_identity_is_rejected(client: TestClient) -> None:
    assert client.get("/jobs").status_code == 401


def test_each_request_sees_only_its_own_organization(client: TestClient, tenants) -> None:  # type: ignore[no-untyped-def]
    a, b = tenants
    seen_a = client.get("/jobs", headers={"x-test-org": str(a.org_id)}).json()
    seen_b = client.get("/jobs", headers={"x-test-org": str(b.org_id)}).json()
    assert str(a.job_id) in seen_a and str(b.job_id) not in seen_a
    assert str(b.job_id) in seen_b and str(a.job_id) not in seen_b
    assert client.get("/org", headers={"x-test-org": str(a.org_id)}).json() == [str(a.org_id)]


def test_unknown_organization_sees_nothing(client: TestClient, tenants) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/jobs", headers={"x-test-org": str(uuid.uuid4())}).json() == []
