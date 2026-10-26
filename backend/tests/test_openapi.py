"""The OpenAPI document: every operation is described and every example in it is accurate."""

import importlib
import inspect
import pkgutil

import pytest
from pydantic import BaseModel

import app.api.routes as routes_package
from app.main import create_app


@pytest.fixture(scope="module")
def spec() -> dict:
    return create_app().openapi()


def models_with_examples() -> list[type[BaseModel]]:
    found = []
    for info in pkgutil.iter_modules(routes_package.__path__):
        module = importlib.import_module(f"app.api.routes.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module.__name__ and (cls.model_config.get("json_schema_extra") or {}).get("examples"):
                found.append(cls)
    return found


def test_every_example_validates_against_its_own_model() -> None:
    models = models_with_examples()
    assert {m.__name__ for m in models} >= {"JobCreate", "RunRequest", "Queued", "MappingPatch", "BulkApprove", "PreviewRequest", "UserCreate", "RoleChange", "LeadIn", "PublicMetrics", "PlanChange", "InvoiceStatusChange", "PublicPlan", "RetentionIn"}
    for model in models:
        for example in model.model_config["json_schema_extra"]["examples"]:  # type: ignore[index]
            instance = model.model_validate(example)
            assert instance is not None, model.__name__


def test_examples_appear_in_the_document(spec) -> None:  # type: ignore[no-untyped-def]
    schemas = spec["components"]["schemas"]
    for name in ("JobCreate", "RunRequest", "LeadIn", "UserCreate", "PublicMetrics"):
        assert schemas[name]["examples"], name


def test_every_operation_is_tagged_and_described(spec) -> None:  # type: ignore[no-untyped-def]
    declared = {t["name"] for t in spec["tags"]}
    count = 0
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            count += 1
            assert operation.get("tags"), f"{method} {path} has no tag"
            assert set(operation["tags"]) <= declared | {"ops"}, f"{method} {path} uses an undeclared tag"
            assert operation.get("summary") or operation.get("description"), f"{method} {path} is not described"
            assert any(code.startswith("2") for code in operation["responses"]), f"{method} {path} documents no success response"
    assert count >= 84


def test_the_document_names_the_error_format_and_csrf_header(spec) -> None:  # type: ignore[no-untyped-def]
    description = spec["info"]["description"]
    assert "X-Requested-With" in description and '"error"' in description
