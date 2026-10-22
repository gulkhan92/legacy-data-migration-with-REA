"""Drive a whole migration through the API for the tests of Phases 8 and later."""

import json
from pathlib import Path
from typing import Any

from tests.test_mapping_api import profiled_clinic_job, profiled_commerce_job  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "datasets" / "fixtures"
EXPECTED = ROOT / "datasets" / "expected"

# What a person supplies during review: the meaning of codes that no data file explains.
COMMERCE_CODES = {"orders.status": {"1": "completed", "2": "pending", "3": "cancelled"}}
CLINIC_CODES = {"encounters.encounter_class": {"1": "ambulatory", "2": "emergency", "3": "inpatient", "4": "wellness", "5": "urgentcare",
                                                 "6": "outpatient", "7": "virtual", "8": "hospice", "9": "home", "10": "snf"}}  # fmt: skip


def truth(kind: str) -> dict[str, Any]:
    return json.loads((FIXTURES / kind / "ground_truth.json").read_text(encoding="utf-8"))


# Where the engine alone gets it wrong and a reviewer steps in: None rejects the mapping, "table.field" overrides the target.
COMMERCE_REVIEW: dict[tuple[str, str], str | None] = {("PROD_MST", "comp_nm"): None, ("PROD_MST", "prod_nm"): "products.name"}


def review(client, job: str, schema: str, corrections: dict[tuple[str, str], str | None]) -> None:  # type: ignore[no-untyped-def]
    schema_id = next(s["id"] for s in client.get("/api/v1/target-schemas").json() if s["name"] == schema)
    fields = {f"{f['table']}.{f['name']}": f["id"] for f in client.get(f"/api/v1/target-schemas/{schema_id}/fields").json()}
    for m in client.get(f"/api/v1/jobs/{job}/mappings").json():
        key = (m["source_table"], m["source_column"])
        if key not in corrections:
            continue
        target = corrections[key]
        if target is None:
            response = client.post(f"/api/v1/jobs/{job}/mappings/{m['id']}/reject")
        else:
            response = client.post(f"/api/v1/jobs/{job}/mappings/{m['id']}/approve", json={"target_field_id": fields[target]})
        assert response.status_code == 200, response.text


def prepare(client, job: str, schema: str, codes: dict[str, dict[str, str]], corrections: dict[tuple[str, str], str | None] | None = None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Map, approve what the engine settled, review, generate rules, supply the code maps, dry run. Returns the dry run."""
    assert client.post(f"/api/v1/jobs/{job}/map", json={"target_schema": schema}).status_code == 200
    assert client.post(f"/api/v1/jobs/{job}/mappings/approve-decided").status_code == 200
    review(client, job, schema, corrections or {})
    generated = client.post(f"/api/v1/jobs/{job}/rules/generate")
    assert generated.status_code == 200, generated.text
    for rule in client.get(f"/api/v1/jobs/{job}/rules").json():
        key = f"{rule['target_table']}.{rule['target_field']}"
        if rule["status"] == "needs_input" and key in codes:
            response = client.put(f"/api/v1/jobs/{job}/rules/{rule['id']}/code-map", json={"mapping": codes[key]})
            assert response.status_code == 200, response.text
    response = client.post(f"/api/v1/jobs/{job}/dry-run")
    assert response.status_code == 200, response.text
    return response.json()
