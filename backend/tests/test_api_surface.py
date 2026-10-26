"""Every route of the API, with the role it needs. A new route fails these tests until it is classified here.

For each route: no sign in gives 401, a role that is too low gives 403, and a sufficient role is never turned away by
the guard (whatever else the request gets wrong, such as an unknown id or a missing body).
"""

import re
import uuid

import pytest

from app.main import create_app
from tests.conftest import login_as
from tests.test_roles import _api_routes

PUBLIC, ANY, ANALYST, ADMIN = "public", "any", "analyst", "admin"
RANK = {"viewer": 1, "analyst": 2, "admin": 3}
NEEDS = {ANY: 1, ANALYST: 2, ADMIN: 3}

SURFACE: dict[tuple[str, str], str] = {
    # auth
    ("POST", "/api/v1/auth/login"): PUBLIC, ("POST", "/api/v1/auth/refresh"): PUBLIC, ("POST", "/api/v1/auth/logout"): PUBLIC, ("GET", "/api/v1/auth/me"): ANY,
    # public
    ("GET", "/api/v1/public/metrics"): PUBLIC, ("POST", "/api/v1/public/leads"): PUBLIC, ("GET", "/health"): PUBLIC,
    # sources
    ("GET", "/api/v1/sources"): ANY, ("GET", "/api/v1/sources/samples"): ANY, ("GET", "/api/v1/sources/{source_id}"): ANY,
    ("POST", "/api/v1/sources/upload"): ANALYST, ("POST", "/api/v1/sources/samples/{name}"): ANALYST, ("DELETE", "/api/v1/sources/{source_id}"): ANALYST,
    ("POST", "/api/v1/sources/{source_id}/discover"): ANALYST, ("POST", "/api/v1/sources/{source_id}/profile"): ANALYST,
    # jobs
    ("GET", "/api/v1/jobs"): ANY, ("POST", "/api/v1/jobs"): ANALYST, ("GET", "/api/v1/jobs/{job_id}"): ANY, ("POST", "/api/v1/jobs/{job_id}/cancel"): ANALYST,
    ("POST", "/api/v1/jobs/{job_id}/run"): ANALYST, ("POST", "/api/v1/jobs/{job_id}/stages/{stage}"): ANALYST,
    ("GET", "/api/v1/jobs/{job_id}/events"): ANY, ("GET", "/api/v1/jobs/{job_id}/events/stream"): ANY,
    # discovery, profiling
    ("GET", "/api/v1/jobs/{job_id}/discovery"): ANY, ("GET", "/api/v1/jobs/{job_id}/discovery/evidence"): ANY,
    ("POST", "/api/v1/jobs/{job_id}/profile"): ANALYST, ("GET", "/api/v1/jobs/{job_id}/profile"): ANY, ("GET", "/api/v1/jobs/{job_id}/quality"): ANY,
    # mappings and schemas
    ("POST", "/api/v1/jobs/{job_id}/map"): ANALYST, ("GET", "/api/v1/jobs/{job_id}/mappings"): ANY, ("PATCH", "/api/v1/jobs/{job_id}/mappings/{mapping_id}"): ANALYST,
    ("POST", "/api/v1/jobs/{job_id}/mappings/approve-decided"): ANALYST, ("POST", "/api/v1/jobs/{job_id}/mappings/bulk-approve"): ANALYST,
    ("POST", "/api/v1/jobs/{job_id}/mappings/{mapping_id}/approve"): ANALYST, ("POST", "/api/v1/jobs/{job_id}/mappings/{mapping_id}/reject"): ANALYST,
    ("GET", "/api/v1/target-schemas"): ANY, ("GET", "/api/v1/target-schemas/{schema_id}/fields"): ANY,
    # transform, load, reports
    ("POST", "/api/v1/jobs/{job_id}/rules/generate"): ANALYST, ("GET", "/api/v1/jobs/{job_id}/rules"): ANY, ("PUT", "/api/v1/jobs/{job_id}/rules/{rule_id}"): ANALYST,
    ("PUT", "/api/v1/jobs/{job_id}/rules/{rule_id}/code-map"): ANALYST, ("POST", "/api/v1/jobs/{job_id}/transforms/preview"): ANALYST,
    ("POST", "/api/v1/jobs/{job_id}/dry-run"): ANALYST, ("POST", "/api/v1/jobs/{job_id}/load"): ANALYST, ("POST", "/api/v1/jobs/{job_id}/rollback"): ANALYST,
    ("GET", "/api/v1/jobs/{job_id}/reconciliation"): ANY, ("GET", "/api/v1/jobs/{job_id}/quarantine"): ANY, ("GET", "/api/v1/jobs/{job_id}/quarantine.csv"): ANY,
    # billing of the caller's organization, public pricing, analytics
    ("GET", "/api/v1/billing/subscription"): ANY, ("GET", "/api/v1/billing/usage"): ANY, ("POST", "/api/v1/billing/subscription/plan"): ADMIN,
    ("GET", "/api/v1/billing/invoices"): ADMIN, ("GET", "/api/v1/billing/invoices/{invoice_id}"): ADMIN, ("PATCH", "/api/v1/billing/invoices/{invoice_id}"): ADMIN,
    ("GET", "/api/v1/public/plans"): PUBLIC, ("GET", "/api/v1/public/pricing/estimate"): PUBLIC,
    ("GET", "/api/v1/analytics/revenue"): ADMIN, ("GET", "/api/v1/analytics/revenue.csv"): ADMIN, ("GET", "/api/v1/analytics/revenue/breakdown"): ADMIN,
    ("GET", "/api/v1/analytics/customers/top"): ADMIN, ("GET", "/api/v1/analytics/invoices"): ADMIN, ("GET", "/api/v1/analytics/invoices.csv"): ADMIN,
    ("GET", "/api/v1/analytics/operations"): ANALYST, ("GET", "/api/v1/analytics/operations.csv"): ANALYST,
    # the console's own views, organization switching and retention
    ("GET", "/api/v1/overview"): ANY, ("GET", "/api/v1/notifications"): ANY, ("GET", "/api/v1/mapping-memory"): ANY, ("DELETE", "/api/v1/mapping-memory/{memory_id}"): ANALYST,
    ("GET", "/api/v1/auth/organizations"): ANY, ("POST", "/api/v1/auth/switch"): ANY, ("GET", "/api/v1/admin/retention"): ADMIN, ("PUT", "/api/v1/admin/retention"): ADMIN,
    ("GET", "/api/v1/analytics/operations/detail"): ANALYST, ("GET", "/api/v1/analytics/revenue/cohorts"): ADMIN,
    # admin
    ("GET", "/api/v1/admin/audit-log"): ADMIN, ("GET", "/api/v1/admin/leads"): ADMIN, ("GET", "/api/v1/admin/users"): ADMIN, ("POST", "/api/v1/admin/users"): ADMIN,
    ("PATCH", "/api/v1/admin/users/{user_id}"): ADMIN, ("DELETE", "/api/v1/admin/users/{user_id}"): ADMIN,
    ("GET", "/api/v1/admin/llm-settings"): ADMIN, ("PUT", "/api/v1/admin/llm-settings"): ADMIN, ("POST", "/api/v1/admin/llm-settings/test-connection"): ADMIN,
    ("GET", "/api/v1/admin/llm-usage"): ADMIN,
}  # fmt: skip

FILL = {"stage": "discover", "name": "commerce"}


def actual_routes() -> set[tuple[str, str]]:
    found = set()
    for route in _api_routes(create_app()):
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found.add((method, route.path))
    return found


def url(template: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: FILL.get(m.group(1)) or str(uuid.uuid4()), template)


def test_the_surface_lists_exactly_the_routes_of_the_app() -> None:
    actual = actual_routes()
    assert actual - set(SURFACE) == set(), "classify these new routes in SURFACE"
    assert set(SURFACE) - actual == set(), "these routes no longer exist"
    assert len(SURFACE) >= 84


@pytest.mark.parametrize("method,template", sorted((m, p) for (m, p), need in SURFACE.items() if need != PUBLIC))
def test_signed_out_callers_are_refused(client, method, template) -> None:  # type: ignore[no-untyped-def]
    assert client.request(method, url(template)).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "analyst", "admin"])
@pytest.mark.parametrize("method,template", sorted((m, p) for (m, p), need in SURFACE.items() if need != PUBLIC))
def test_each_role_gets_in_exactly_where_it_should(client, api_users, role, method, template) -> None:  # type: ignore[no-untyped-def]
    need = SURFACE[(method, template)]
    assert login_as(client, f"{role}@acme.test").status_code == 200
    status = client.request(method, url(template)).status_code
    if RANK[role] >= NEEDS[need]:
        assert status not in (401, 403), f"{role} was refused on {method} {template}"
    else:
        assert status == 403, f"{role} got {status} on {method} {template}"


def test_public_routes_need_no_sign_in(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/public/metrics").status_code == 200
    assert client.post("/api/v1/public/leads", json={"email": "someone@example.com"}).status_code == 201
