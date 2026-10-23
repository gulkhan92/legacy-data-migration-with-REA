"""A whole migration from API calls only, on the commerce sample system: register the sample, create the job, run the
stages in the background, review the mappings and rules, preview a rule, dry run, load, export the quarantine, roll back."""

import csv
import io
import json
import shutil

import pytest

from app.api.routes.transform import _safe_cell
from app.discovery.rea_adapter import REPO_ROOT
from tests.conftest import login_as
from tests.test_jobs_api import sse
from tests.test_mapping_api import seeded  # noqa: F401
from tests.transform_helpers import COMMERCE_CODES

REA_INSTALLED = (REPO_ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
requires_rea = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")


@pytest.fixture(autouse=True)
def _forget_what_was_approved(admin_engine):  # type: ignore[no-untyped-def]
    """Approving mappings teaches the organization's mapping memory; later tests start from an empty one."""
    yield
    from sqlalchemy import text

    with admin_engine.begin() as connection:
        connection.execute(text("DELETE FROM mapping_memory WHERE org_id IN (SELECT id FROM organizations WHERE name = 'Acme')"))


def get_status(client, job: str) -> str:  # type: ignore[no-untyped-def]
    return client.get(f"/api/v1/jobs/{job}").json()["status"]


@requires_rea
def test_a_full_job_runs_from_api_calls_only(client, seeded, admin_engine) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200

    sample = client.post("/api/v1/sources/samples/commerce").json()
    created = client.post("/api/v1/jobs", json={"source_id": sample["source_id"], "target_schema": sample["target_schema"]})
    assert created.status_code == 201
    job = created.json()["id"]

    # discover, profile and map run in the background; the job ends up waiting for a person
    queued = client.post(f"/api/v1/jobs/{job}/run", json={"stages": ["discover", "profile", "map"]})
    assert queued.status_code == 202 and queued.json()["stages"] == ["discover", "profile", "map"]
    assert get_status(client, job) == "MAPPED"
    assert client.get(f"/api/v1/jobs/{job}").json()["next_stage"] == "rules"

    # review: the engine paired the composer column with the product name; a person corrects it
    schema_id = next(s["id"] for s in client.get("/api/v1/target-schemas").json() if s["name"] == "commerce")
    fields = {f"{f['table']}.{f['name']}": f["id"] for f in client.get(f"/api/v1/target-schemas/{schema_id}/fields").json()}
    mappings = {(m["source_table"], m["source_column"]): m for m in client.get(f"/api/v1/jobs/{job}/mappings").json()}
    wrong, right = mappings[("PROD_MST", "comp_nm")], mappings[("PROD_MST", "prod_nm")]
    assert client.patch(f"/api/v1/jobs/{job}/mappings/{wrong['id']}", json={"target_field_id": None}).json()["status"] == "rejected"
    fixed = client.patch(f"/api/v1/jobs/{job}/mappings/{right['id']}", json={"target_field_id": fields["products.name"], "transform_hint": "product name"})
    assert fixed.json()["status"] == "overridden" and fixed.json()["transform_hint"] == "product name"
    other_schema = next(s["id"] for s in client.get("/api/v1/target-schemas").json() if s["name"] == "clinic")
    clinic_field = next(f["id"] for f in client.get(f"/api/v1/target-schemas/{other_schema}/fields").json())
    assert client.patch(f"/api/v1/jobs/{job}/mappings/{right['id']}", json={"target_field_id": clinic_field}).status_code == 422

    todo = [m["id"] for m in client.get(f"/api/v1/jobs/{job}/mappings").json() if m["target"] and m["status"] == "suggested"]
    bulk = client.post(f"/api/v1/jobs/{job}/mappings/bulk-approve", json={"mapping_ids": todo})
    assert bulk.status_code == 200 and bulk.json()["approved"] == len(todo) > 0
    assert client.post(f"/api/v1/jobs/{job}/mappings/bulk-approve", json={"mapping_ids": [wrong["id"]]}).json()["skipped"][0]["reason"] == "rejected"
    assert client.post(f"/api/v1/jobs/{job}/mappings/bulk-approve", json={"mapping_ids": ["00000000-0000-4000-8000-000000000000"]}).status_code == 404

    # rules: generated in the background, one needs a code map that no data file can explain
    assert client.post(f"/api/v1/jobs/{job}/stages/rules").status_code == 202
    assert get_status(client, job) == "AWAITING_REVIEW"
    assert client.post(f"/api/v1/jobs/{job}/stages/dry_run").status_code == 202
    assert get_status(client, job) == "AWAITING_REVIEW"  # blocked, waiting for the code map
    blocked = [e for e in client.get(f"/api/v1/jobs/{job}/events").json()["items"] if e["detail"].get("outcome") == "blocked"]
    assert blocked and "orders.status" in blocked[-1]["message"]

    rules = {(r["target_table"], r["target_field"]): r for r in client.get(f"/api/v1/jobs/{job}/rules").json()}
    status_rule = rules[("orders", "status")]
    assert status_rule["status"] == "needs_input"
    assert client.put(f"/api/v1/jobs/{job}/rules/{status_rule['id']}/code-map", json={"mapping": COMMERCE_CODES["orders.status"]}).status_code == 200

    # try a rule on a sample without saving it
    date_rule = rules[("orders", "order_date")]
    body = {"target_table": "orders", "target_field": "order_date"}
    preview = client.post(f"/api/v1/jobs/{job}/transforms/preview", json={**body, "rows": 5})
    assert preview.status_code == 200 and preview.json()["sampled"] > 0 and preview.json()["rows"]
    narrow = client.post(f"/api/v1/jobs/{job}/transforms/preview", json={**body, "steps": [{"op": "parse_date", "formats": ["%Y-%m-%d"]}]})
    assert narrow.status_code == 200 and narrow.json()["failed"] > preview.json()["failed"]  # fewer formats, more failures
    assert any(r["failed"] and r["kind"] == "invalid_date" for r in narrow.json()["rows"])
    assert client.post(f"/api/v1/jobs/{job}/transforms/preview", json={**body, "steps": [{"op": "eval"}]}).status_code == 422
    assert client.post(f"/api/v1/jobs/{job}/transforms/preview", json={"target_table": "orders", "target_field": "nope"}).status_code == 404
    saved = next(r for r in client.get(f"/api/v1/jobs/{job}/rules").json() if r["id"] == date_rule["id"])
    assert saved["steps"] == date_rule["steps"]  # nothing was saved

    # dry run and load in the background
    run = client.post(f"/api/v1/jobs/{job}/run", json={"stages": ["dry_run", "load"]})
    assert run.status_code == 202
    assert get_status(client, job) == "COMPLETED"
    assert client.post(f"/api/v1/jobs/{job}/stages/load").status_code == 409  # a finished job takes no more stages

    report = client.get(f"/api/v1/jobs/{job}/reconciliation").json()
    assert report["passed"] is True and report["loaded_rows"] + report["quarantined_rows"] == report["source_rows"] == 6338

    # the load was metered: one usage event for the job, on a plan, and the day's metrics count its records
    usage = client.get("/api/v1/billing/usage").json()
    [event] = [e for e in usage["items"] if e["job_id"] == job]
    assert event["records_loaded"] == report["loaded_rows"] > 0
    assert client.get("/api/v1/billing/subscription").json()["usage"]["records"] >= report["loaded_rows"]
    from sqlalchemy import text

    with admin_engine.connect() as connection:
        today = connection.execute(text("SELECT platform_day_metrics(CURRENT_DATE)")).scalar_one()
    assert today["records"] >= report["loaded_rows"] and today["active_orgs"] >= 1

    # quarantine export
    exported = client.get(f"/api/v1/jobs/{job}/quarantine.csv")
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/csv")
    assert exported.headers["content-disposition"].startswith("attachment") and exported.headers["x-content-type-options"] == "nosniff"
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert rows[0] == ["table", "row", "reason", "detail", "raw_row"] and len(rows) - 1 == report["quarantined_rows"]
    assert all(r[2] and json.loads(r[4]) for r in rows[1:])
    filtered = list(csv.reader(io.StringIO(client.get(f"/api/v1/jobs/{job}/quarantine.csv?reason=invalid_date&table=orders").text)))
    assert len(filtered) - 1 == 12

    # the whole history is there, in order, and the stream of a finished job replays it and ends
    events = client.get(f"/api/v1/jobs/{job}/events").json()["items"]
    statuses = [e["to_status"] for e in events if e["from_status"] != e["to_status"]]
    assert statuses[0] == "UPLOADED" and statuses[-1] == "COMPLETED" and "LOADING" in statuses and "RECONCILING" in statuses
    streamed = sse(client, f"/api/v1/jobs/{job}/events/stream")
    assert len([e for e in streamed if e.get("event") == "job"]) == len(events) and streamed[-1]["event"] == "end"

    # rollback by job id, through the synchronous endpoint
    rolled = client.post(f"/api/v1/jobs/{job}/rollback")
    assert rolled.status_code == 200 and get_status(client, job) == "ROLLED_BACK"
    assert sum(rolled.json()["removed"].values()) == report["loaded_rows"]
    assert job not in [e["job_id"] for e in client.get("/api/v1/billing/usage").json()["items"]]  # a rolled back load is not billed


@pytest.mark.parametrize("value,expected", [("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2", "'-2"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"), ("plain", "plain"), (None, ""), (12, "12")])
def test_csv_cells_cannot_become_formulas(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert _safe_cell(value) == expected


@requires_rea
def test_a_two_file_source_runs_through_the_stages(client, seeded) -> None:  # type: ignore[no-untyped-def]
    assert login_as(client, "analyst@acme.test").status_code == 200
    sample = client.post("/api/v1/sources/samples/clinic").json()
    job = client.post("/api/v1/jobs", json={"source_id": sample["source_id"], "data_source_id": sample["data_source_id"], "target_schema": "clinic"}).json()["id"]
    assert client.post(f"/api/v1/jobs/{job}/run", json={"stages": ["discover", "profile", "map", "rules"]}).status_code == 202
    state = client.get(f"/api/v1/jobs/{job}").json()
    assert state["status"] == "AWAITING_REVIEW" and state["data_source_id"] == sample["data_source_id"]
    rules = client.get(f"/api/v1/jobs/{job}/rules").json()
    assert {r["target_table"] for r in rules} == {"patients", "encounters", "conditions", "medications", "observations"}
    assert client.post(f"/api/v1/jobs/{job}/cancel").status_code == 200  # leave nothing half done
