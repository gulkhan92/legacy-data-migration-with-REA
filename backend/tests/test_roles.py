import io

import pytest

from tests.conftest import login_as

SQLITE_BYTES = b"SQLite format 3\x00" + b"\x00" * 200

# (method, path, kwargs, roles allowed). Authorization is decided before the request body is used.
MATRIX = [
    ("get", "/api/v1/auth/me", {}, {"admin", "analyst", "viewer"}),
    ("get", "/api/v1/sources", {}, {"admin", "analyst", "viewer"}),
    ("post", "/api/v1/sources/upload", {"files": {"file": ("t.db", SQLITE_BYTES)}}, {"admin", "analyst"}),
    ("get", "/api/v1/admin/audit-log", {}, {"admin"}),
]


@pytest.mark.parametrize("role", ["admin", "analyst", "viewer"])
@pytest.mark.parametrize("method,path,kwargs,allowed", MATRIX, ids=[m[1] for m in MATRIX])
def test_role_matrix(client, role, method, path, kwargs, allowed) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, f"{role}@acme.test").status_code == 200
    response = getattr(client, method)(path, **{k: v for k, v in kwargs.items()})
    if role in allowed:
        assert response.status_code in (200, 201), response.text
    else:
        assert response.status_code == 403


@pytest.mark.parametrize("method,path,kwargs,allowed", MATRIX, ids=[m[1] for m in MATRIX])
def test_anonymous_requests_are_rejected(client, method, path, kwargs, allowed) -> None:  # type: ignore[no-untyped-def]
    kwargs = {k: (v if k != "files" else {"file": ("t.db", io.BytesIO(SQLITE_BYTES))}) for k, v in kwargs.items()}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def _api_routes(app):  # type: ignore[no-untyped-def]
    """Every APIRoute, including those registered through include_router (wrapped by newer FastAPI versions)."""
    for route in app.routes:
        if hasattr(route, "original_router"):
            yield from route.original_router.routes
        elif hasattr(route, "dependant"):
            yield route


def test_every_api_route_is_guarded() -> None:
    """Fails when a new route is added without an authentication or role dependency."""
    from app.main import create_app

    public = {"/health", "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout", "/api/v1/public/metrics", "/api/v1/public/leads"}

    def deps(dependant):  # type: ignore[no-untyped-def]
        for sub in dependant.dependencies:
            yield sub.call
            yield from deps(sub)

    checked = []
    for route in _api_routes(create_app()):
        path = route.path
        if path in public:
            continue
        names = {getattr(c, "__name__", "") for c in deps(route.dependant)}
        assert {"get_principal", "dependency"} & names, f"{path} has no auth dependency"
        checked.append(path)
    assert len(checked) >= 7  # the guard must really see the routes
    assert "/api/v1/sources/{source_id}/discover" in checked
