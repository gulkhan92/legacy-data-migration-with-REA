"""A client that reads right after a write must see the write.

The session of a request is committed when the route function returns, before the response is sent. With the default scope of a FastAPI
dependency that yields, the commit came after the response had gone out; a page that reloaded its list the moment a save succeeded could read the
old data. The console found it: it overrode a mapping and the table, refetched at once, still showed the old target.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from tests.conftest import CSRF, login_as
from tests.test_mapping_api import seeded  # noqa: F401


def test_the_commit_comes_before_the_response_starts(client, api_users, seeded, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    source = client.post("/api/v1/sources/upload", files={"file": ("p.csv", b"id,name\n1,a\n")}).json()

    events: list[str] = []
    real_commit = Session.commit

    def spying_commit(self: Session) -> None:
        real_commit(self)
        events.append("commit")

    monkeypatch.setattr(Session, "commit", spying_commit)

    app = client.app

    class Spy:
        def __init__(self, inner):  # type: ignore[no-untyped-def]
            self.inner = inner

        async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
            async def spy_send(message):  # type: ignore[no-untyped-def]
                if message["type"] == "http.response.start":
                    events.append("response")
                await send(message)

            await self.inner(scope, receive, spy_send)

    with TestClient(Spy(app), base_url="https://testserver", headers=CSRF) as spy_client:
        spy_client.cookies.update(client.cookies)
        created = spy_client.post("/api/v1/jobs", json={"source_id": source["id"], "target_schema": "commerce"})
    assert created.status_code == 201
    assert "commit" in events and "response" in events
    assert events.index("commit") < events.index("response"), events


def test_every_route_that_opens_a_tenant_session_closes_it_before_responding() -> None:
    """The scope is declared on each use, so a new route that forgets it is caught here."""
    from tests.test_roles import _api_routes

    checked = 0
    for route in _api_routes(create_app()):
        for dependency in route.dependant.dependencies:
            if getattr(dependency.call, "__name__", "") == "get_tenant_db":
                checked += 1
                assert dependency.scope == "function", f"{route.methods} {route.path} keeps the session open until after the response"
    assert checked > 20
