"""Transform, load and reconcile both fixtures through the API and check the outcome against the injected defects."""

import collections
import shutil

import pytest

from app.discovery.rea_adapter import REPO_ROOT
from tests.conftest import login_as
from tests.test_mapping_api import seeded  # noqa: F401
from tests.transform_helpers import CLINIC_CODES, COMMERCE_CODES, COMMERCE_REVIEW, prepare, profiled_clinic_job, profiled_commerce_job, truth

REA_INSTALLED = (REPO_ROOT / "rea-runner" / "node_modules" / "rea-agents" / "package.json").exists() and shutil.which("node")
pytestmark = pytest.mark.skipif(not REA_INSTALLED, reason="REA runner not installed: run `npm ci` in rea-runner")

COMMERCE_TABLES = {"CUST_MST": "customers", "PROD_MST": "products", "INV_HDR": "orders", "INV_DTL": "order_items"}


def injected(kind: str) -> dict[tuple[str, str], int]:
    names = COMMERCE_TABLES if kind == "commerce" else {}
    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    for d in truth(kind)["defects"]:
        counts[(names.get(d["table"], d["table"]), d["type"])] += 1
    return dict(counts)


def run_load(client, job: str, schema: str, codes, corrections=None):  # type: ignore[no-untyped-def]
    dry = prepare(client, job, schema, codes, corrections)
    assert dry["ready"] is True and dry["blocking"] == []
    response = client.post(f"/api/v1/jobs/{job}/load")
    assert response.status_code == 200, response.text
    return response.json()


def check_against_defects(body: dict, kind: str) -> None:
    gt = injected(kind)
    assert body["passed"] is True and body["status"] == "COMPLETED"
    assert body["quality_after"] > body["quality_before"]
    for t in body["tables"]:
        name, reasons = t["target_table"], t["by_reason"]
        # every source row is either loaded or quarantined with a reason, nothing else
        assert t["loaded"] + t["quarantined"] == t["source_rows"] and t["unexplained"] == 0 and t["passed"] is True
        assert sum(reasons.values()) == t["quarantined"]
        for reason in ("required_null", "invalid_date"):
            assert reasons.get(reason, 0) == gt.get((name, reason if reason != "required_null" else "null_required"), 0), (name, reason)
        if (name, kind) != ("patients", "clinic"):  # patients.HEALTHCARE_EXPENSES has no column in the target
            wanted = gt.get((name, "out_of_range"), 0)  # a few real values can fall outside the learned bounds as well
            assert wanted <= reasons.get("out_of_range", 0) <= wanted * 1.2, name
        injected_dups = gt.get((name, "duplicate"), 0)
        # a copy of a row that was itself quarantined carries the original's reason instead of 'duplicate'
        assert 0.75 * injected_dups <= reasons.get("duplicate", 0) <= injected_dups, name
        # an orphan is either injected or follows a parent that did not load
        assert reasons.get("orphan_fk", 0) >= gt.get((name, "orphan_fk"), 0)


def test_commerce_load_matches_injected_defects(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    job = profiled_commerce_job(client)
    body = run_load(client, job, "commerce", COMMERCE_CODES, COMMERCE_REVIEW)
    check_against_defects(body, "commerce")
    sources = {t["target_table"]: t["source_rows"] for t in body["tables"]}
    assert sources == {"customers": 60, "products": 3573, "orders": 420, "order_items": 2285}

    report = client.get(f"/api/v1/jobs/{job}/reconciliation").json()
    assert report["passed"] is True and report["loaded_rows"] == body["loaded"] and report["quarantined_rows"] == body["quarantined"]
    quarantined = client.get(f"/api/v1/jobs/{job}/quarantine?reason=invalid_date&table=orders&limit=5").json()
    assert quarantined["total"] == 12 and all(r["raw"] and r["detail"] for r in quarantined["rows"])

    # a second load of the same job is refused, and a rollback by job id empties the warehouse again
    again = client.post(f"/api/v1/jobs/{job}/load")
    assert again.status_code == 409
    rolled = client.post(f"/api/v1/jobs/{job}/rollback")
    assert rolled.status_code == 200 and sum(rolled.json()["removed"].values()) == body["loaded"]
    assert rolled.json()["quarantined_removed"] == body["quarantined"] and rolled.json()["status"] == "ROLLED_BACK"
    assert client.get(f"/api/v1/jobs/{job}/reconciliation").status_code == 404
    assert client.post(f"/api/v1/jobs/{job}/rollback").status_code == 409


def test_clinic_load_matches_injected_defects(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    job = profiled_clinic_job(client)
    body = run_load(client, job, "clinic", CLINIC_CODES)
    check_against_defects(body, "clinic")
    assert {t["target_table"]: t["source_rows"] for t in body["tables"]} == {"patients": 306, "encounters": 7855, "conditions": 5391, "medications": 6228, "observations": 6120}


def test_unmapped_codes_block_the_load_until_a_person_supplies_them(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    job = profiled_commerce_job(client)
    prepare_without_codes = client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": "commerce"})
    assert prepare_without_codes.status_code == 200
    client.post(f"/api/v1/jobs/{job}/mappings/approve-decided")
    assert client.post(f"/api/v1/jobs/{job}/rules/generate").status_code == 200
    needing = [r for r in client.get(f"/api/v1/jobs/{job}/rules").json() if r["status"] == "needs_input"]
    assert [(r["target_table"], r["target_field"]) for r in needing] == [("orders", "status")]
    assert needing[0]["failures"]["missing"]["codes"] == ["1", "2", "3"]
    dry = client.post(f"/api/v1/jobs/{job}/dry-run")
    assert dry.status_code == 200 and dry.json()["ready"] is False and "orders.status" in dry.json()["blocking"][0]
    blocked = client.post(f"/api/v1/jobs/{job}/load")
    assert blocked.status_code == 409 and "READY" in blocked.json()["detail"]["message"]


def test_load_is_for_analysts_and_stays_inside_the_organization(client, seeded) -> None:  # type: ignore[no-untyped-def]
    login_as(client, "analyst@acme.test")
    job = profiled_commerce_job(client)
    client.cookies.clear()
    login_as(client, "viewer@acme.test")
    for path in ("rules/generate", "dry-run", "load", "rollback", "mappings/approve-decided"):
        assert client.post(f"/api/v1/jobs/{job}/{path}").status_code == 403, path
    assert client.get(f"/api/v1/jobs/{job}/rules").status_code == 200
    client.cookies.clear()
    login_as(client, "admin@globex.test")
    assert client.get(f"/api/v1/jobs/{job}/rules").status_code == 404
    assert client.post(f"/api/v1/jobs/{job}/load").status_code == 404
