import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.deps as deps
from app.api.deps import ACCESS_COOKIE, get_tenant_db
from app.core.security import Principal, create_access_token
from app.db.models.enums import Role
from app.db import models


def _cookie(org_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(Principal(uuid.uuid4(), org_id, Role.VIEWER))
    return {ACCESS_COOKIE: token}


@pytest.fixture
def client(app_factory, monkeypatch) -> TestClient:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(deps, "make_session_factory", lambda: app_factory)
    api = FastAPI()

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
    seen_a = client.get("/jobs", cookies=_cookie(a.org_id)).json()
    seen_b = client.get("/jobs", cookies=_cookie(b.org_id)).json()
    assert str(a.job_id) in seen_a and str(b.job_id) not in seen_a
    assert str(b.job_id) in seen_b and str(a.job_id) not in seen_b
    assert client.get("/org", cookies=_cookie(a.org_id)).json() == [str(a.org_id)]


def test_unknown_organization_sees_nothing(client: TestClient, tenants) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/jobs", cookies=_cookie(uuid.uuid4())).json() == []
