"""The answers /docs shows (#449): every JSON route has a model, errors are
shown as the app sends them, and the check that keeps them true works."""

import types

from fastapi.responses import JSONResponse

from nautobot_maps.api import ROUTERS
from nautobot_maps.api.answers import common
from tests import answers as answer_check

# No model, on purpose: MCP describes its own tools; CSV and Prometheus text
# aren't JSON.
WITHOUT_MODEL = {"POST /mcp"}
# Areas not described yet, one PR each (#449); each takes its routes off.
PENDING = {
    "GET /api/status/{tenant:path}",
    "GET /api/availability",
    "GET /api/circuit-mapping",
    "GET /api/maintenance",
    "GET /api/maintenance/history",
    "GET /api/maintenance/sites",
    "GET /api/maintenance/devices",
    "POST /api/maintenance",
    "POST /api/maintenance/end",
    "POST /api/maintenance/{window_id:int}/end",
    "GET /api/locations",
    "GET /api/locations/{location_id}/detail",
    "GET /api/location-alerts",
    "GET /api/locations/coordinate-check",
    "GET /api/search",
    "GET /api/alerts",
    "GET /api/criticality-overrides",
    "POST /api/criticality-overrides",
    "DELETE /api/criticality-overrides/{device_id}",
    "GET /api/alert-feed",
    "GET /api/alert-history",
    "POST /api/alert-acks",
    "POST /api/alert-acks/remove",
    "POST /api/alert-cases",
    "POST /api/librenms/alert",
    "POST /api/alertmanager",
    "POST /api/device-status",
    "GET /api/contract",
    "GET /api/tokens",
    "POST /api/tokens",
    "POST /api/tokens/{token_id:int}/revoke",
    "POST /api/notifications/test",
}


def _json_routes():
    for router in ROUTERS:
        for route in router.routes:
            path = route.path
            if not getattr(route, "include_in_schema", False) or path.endswith(".csv"):
                continue
            if path.startswith("/api/") or path in ("/healthz", "/mcp"):
                for method in route.methods:
                    yield f"{method} {path}", route


def test_every_json_route_shows_its_answer():
    without = {key for key, route in _json_routes() if route.response_model is None}
    assert without == WITHOUT_MODEL | PENDING


def test_errors_are_shown_as_the_app_sends_them(client):
    spec = client.get("/openapi.json").get_json()
    error = {"$ref": "#/components/schemas/Error"}
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas and "ValidationError" not in schemas
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            assert "422" not in operation["responses"], f"{method} {path}: the app answers bad input with 400"
    # Bad input: 400 {"error": ...}.
    alerts = spec["paths"]["/api/alerts"]["get"]["responses"]
    assert alerts["400"]["content"]["application/json"]["schema"] == error
    # A role: 401 and 403 too; none without one.
    tokens = spec["paths"]["/api/tokens"]["get"]["responses"]
    assert tokens["401"]["content"]["application/json"]["schema"] == error and "403" in tokens
    assert "401" not in spec["paths"]["/healthz"]["get"]["responses"]
    assert set(schemas["Error"]["properties"]) == {"error"}


def test_the_answer_is_in_the_spec(client):
    spec = client.get("/openapi.json").get_json()
    health = spec["paths"]["/healthz"]["get"]["responses"]
    assert health["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Health"}
    assert health["503"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Health"}
    assert "inventory_sync_age_seconds" in spec["components"]["schemas"]["Health"]["properties"]


class TestTheCheck:
    def _route(self, model, responses=None):
        return types.SimpleNamespace(methods={"GET"}, path="/x", response_model=model, responses=responses or {})

    def test_a_matching_answer_passes(self):
        answer_check.check(self._route(common.Error), JSONResponse({"error": "Nope"}))
        assert answer_check.problems == []

    def test_a_field_not_in_the_model_fails(self):
        answer_check.check(self._route(common.Error), JSONResponse({"error": "Nope", "extra": 1}))
        assert "extra" in answer_check.problems.pop()

    def test_a_wrong_type_fails_also_text_for_a_number(self):
        answer_check.check(self._route(common.Error), JSONResponse({"error": 1}))
        assert answer_check.problems.pop()

    def test_an_error_status_uses_its_own_model(self):
        route = self._route(None, {404: {"model": common.Error}})
        answer_check.check(route, JSONResponse({"error": "Not found"}, status_code=404))
        answer_check.check(route, JSONResponse({"oops": 1}, status_code=404))
        assert len(answer_check.problems) == 1
        answer_check.problems.clear()

    def test_an_undocumented_status_and_a_stream_are_left_alone(self):
        from fastapi.responses import StreamingResponse

        answer_check.check(self._route(common.Error), JSONResponse({"x": 1}, status_code=500))
        answer_check.check(self._route(common.Error), StreamingResponse(iter([b"a"])))
        assert answer_check.problems == []
