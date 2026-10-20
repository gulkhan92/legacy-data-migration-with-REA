import re
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.db import models
from app.seed import DEMO_USERS, seed
from tests.conftest import login_as

ROOT = Path(__file__).resolve().parents[2]


def test_rea_runner_sandbox_is_locked_down() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    runner = compose["services"]["rea-runner"]
    assert runner["network_mode"] == "none"
    assert runner["read_only"] is True
    assert runner["user"] not in ("0", "0:0", "root")
    assert runner["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in runner["security_opt"]
    assert runner["mem_limit"] and runner["cpus"] and runner["pids_limit"]
    assert runner["environment"]["REA_TIMEOUT_SECONDS"] == "120"
    assert all(v.endswith(":ro") for v in runner["volumes"])
    assert re.search(r"^USER 10001", (ROOT / "rea-runner" / "Dockerfile").read_text(), re.M)


# The only module allowed to start a process: it runs the fixed REA runner command and passes the
# uploaded file as an argument. It never runs the file itself.
PROCESS_ALLOWED = {"backend/app/discovery/rea_adapter.py"}


def test_application_never_executes_uploaded_files() -> None:
    forbidden = re.compile(r"\b(subprocess|importlib|ctypes|pythonnet)\b|os\.system|os\.popen|\beval\(|\bexec\(")
    offenders = [
        rel
        for path in (ROOT / "backend" / "app").rglob("*.py")
        if forbidden.search(path.read_text(encoding="utf-8")) and (rel := path.relative_to(ROOT).as_posix()) not in PROCESS_ALLOWED
    ]
    assert offenders == []


def test_the_rea_adapter_never_uses_a_shell_and_never_runs_the_upload() -> None:
    source = (ROOT / "backend" / "app" / "discovery" / "rea_adapter.py").read_text(encoding="utf-8")
    assert "shell=False" in source and "shell=True" not in source
    assert "os.system" not in source and "os.popen" not in source
    # the runner command comes from settings, the uploaded file is only ever one argument after it
    assert "argv = [*command, rea_command, file_arg" in source


def test_seed_creates_the_three_demo_roles_and_is_idempotent(admin_engine, client) -> None:  # type: ignore[no-untyped-def]
    passwords = {env: f"Seed-Passw0rd-{i}-Local" for i, (_, _, _, env) in enumerate(DEMO_USERS)}
    for _ in range(2):
        with Session(admin_engine) as session:
            seed(session, passwords)
    with Session(admin_engine) as session:
        org = session.query(models.Organization).filter_by(slug="demo").one()
        roles = {m.role.value for m in session.query(models.Membership).filter_by(org_id=org.id)}
        assert roles == {"admin", "analyst", "viewer"}
    for (email, _, role, env) in DEMO_USERS:
        response = login_as(client, email, passwords[env])
        assert response.status_code == 200 and response.json()["role"] == role.value
        client.cookies.clear()


def test_registration_endpoint_does_not_exist(client) -> None:  # type: ignore[no-untyped-def]
    for path in ("/api/v1/auth/register", "/api/v1/auth/signup", "/api/v1/users"):
        assert client.post(path, json={"email": "x@y.test", "password": "Passw0rd-long-enough"}).status_code in (404, 405)
