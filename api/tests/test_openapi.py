from pathlib import Path

from app.export_openapi import OUTPUT, render
from app.main import app


def test_spec_is_served(client):
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/items" in spec["paths"]


def test_every_operation_has_stable_id_and_response_schema():
    for path, methods in app.openapi()["paths"].items():
        for method, op in methods.items():
            assert op["operationId"][0].islower() and "_" not in op["operationId"], (path, method)
            ok = [r for code, r in op["responses"].items() if code.startswith("2")]
            assert ok, (path, method)


def test_exported_spec_is_current():
    assert Path(OUTPUT).read_text() == render(), "run: uv run python -m app.export_openapi"


def test_healthz(client):
    assert client.get("/api/healthz").json() == {"status": "ok"}
