"""Ported from nautobot_maps/apidocs.py (#604): imports mapped, API paths under /api/v1. The API explorer at /api/docs (#230, #483): every page and endpoint, and what it takes.

Read from the Flask app's routes, so a new route appears without editing
this module.  Only the example request bodies of write endpoints are listed
by hand; a test fails when a write endpoint has none.
"""

import inspect
import json
import re

from app.services import navigation_service as navigation
from app.services import scope_service as scope

# Endpoints that read a page's ?tenant= / ?q= (#368, #369) through scope.read_scope.
SCOPED_ENDPOINTS = {
    "board.api_alert_feed": scope.QUERY_PARAMS,
    "locations.api_coordinate_check": scope.QUERY_PARAMS,
}

# Example JSON bodies for the write endpoints, keyed by endpoint name.
EXAMPLE_BODIES = {
    "board.api_set_criticality_override": {
        "nautobot_device_id": "<device uuid>",
        "is_critical": True,
        "reason": "Core uplink for the site",
        "updated_by": "noc",
    },
    "ops.mcp_endpoint": {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_alert_board", "arguments": {"filter": "alarms", "limit": 5}},
    },
    "admin.api_notifications_test": {},
    "maintenance.api_maintenance_end_many": {"ids": [1, 2]},
    "maintenance.api_maintenance_create": {
        "site_id": "<site id>",
        "duration_minutes": 120,
        "reason": "Core switch upgrade",
    },
    "maintenance.api_maintenance_end": {},
    "ingest.api_librenms_alert": {"device_id": 42},
    "ingest.api_alertmanager": {
        "version": "4",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "InstanceDown", "instance": "sw1.example.com:9100"},
                "annotations": {"summary": "Target down"},
                "startsAt": "2026-10-09T11:50:00Z",
                "fingerprint": "a1b2c3",
            }
        ],
    },
    "ingest.api_device_status": {
        "device": "sw1.example.com",
        "status": "down",
        "source": "zabbix",
        "id": "123",
    },
    "admin.api_tokens_create": {"name": "librenms", "role": "operator", "expires_in_days": 365},
    "admin.api_tokens_revoke": {},
    "admin.api_tokens_purge": {"older_than_days": 90, "dry_run": True},
    # The new stack's own routes (#592, #608 review), by their route names.
    "create_token": {"name": "librenms", "role": "operator", "expires_in_days": 365},
    "revoke_token": {},
    "purge_tokens": {"older_than_days": 90, "dry_run": True},
    "create_item": {"name": "Rack 12", "description": "The example resource (#572)"},
    "admin.api_status_link_replace": {},
    "admin.api_wireless_check": {},
    "board.api_acknowledge": {"site_id": "<site id>", "note": "On my way"},
    "board.api_unacknowledge": {"site_id": "<site id>"},
    "board.api_add_alert_case": {
        "site_id": "<site id>",
        "device_ids": ["<device uuid>"],
        "case_number": "INC-1234",
    },
}

GROUPS = ("Pages", "API", "Monitoring")
MONITORING_PATHS = {"/healthz", "/metrics"}
HIDDEN_ENDPOINTS = {
    "static",
    "frontend.asset",
    "pages.api_docs_moved",
    "ops.openapi_moved",
    # /healthz's old path, until the switchover (#594); and web's files.
    "healthz_alias",
    "asset",
}
IGNORED_METHODS = {"HEAD", "OPTIONS"}
# " (#180)" / " (#227, #228)": issue references, for developers, not readers.
_ISSUE_REFS = re.compile(r"\s*\(#\d+(?:,\s*#\d+)*\)")


def _group(path: str) -> str:
    if path in MONITORING_PATHS:
        return "Monitoring"
    if path in navigation.PAGES_UNDER_API:
        return "Pages"
    return "API" if path.startswith("/api/") or path == "/mcp" else "Pages"


def _flask_style(path: str, endpoint) -> str:
    """A FastAPI path as Flask writes it (``{tenant:path}`` is ``<path:tenant>``,
    an ``int`` parameter ``<int:id>``), so both read the same here."""
    hints = inspect.signature(endpoint).parameters

    def convert(match):
        name, _, kind = match.group(1).partition(":")
        if not kind and name in hints and hints[name].annotation is int:
            kind = "int"
        return f"<{kind}:{name}>" if kind else f"<{name}>"

    return re.sub(r"\{([^}]+)\}", convert, path)


def _fastapi_query_params(dependant) -> list[str]:
    """A FastAPI route's query parameters by name, a query model's fields one
    by one (``from``, not ``from_``), its dependencies' too."""
    from pydantic import BaseModel

    names = []
    for param in dependant.query_params:
        annotation = param.field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            names += [field.alias or name for name, field in annotation.model_fields.items()]
        else:
            names.append(param.alias)
    for sub in dependant.dependencies:
        names += _fastapi_query_params(sub)
    return list(dict.fromkeys(names))


def routes(app=None) -> list[dict]:
    """Every route the app serves (#436; *app* is ignored, kept for callers):
    ``path`` (Flask's notation), ``methods``, ``endpoint``, ``view``,
    ``path_params``, ``query_params``, ``required_role``, ``tags``."""
    found = []
    from fastapi.routing import APIRoute

    from app.main import app as whole_app
    from app.v1 import deps

    # Every route the app has (#608 review: also the new stack's own, tokens, contract,
    # /me), its full path from the app's spec by operation id: FastAPI keeps included
    # routers as private objects, prefixes apart.
    full_paths = {
        operation["operationId"]: path
        for path, operations in whole_app.openapi()["paths"].items()
        for operation in operations.values()
    }

    def every(routes_):  # type: ignore[no-untyped-def]
        for route in routes_:
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from every(original.routes)
            elif isinstance(route, APIRoute):
                yield route

    for route in every(whole_app.routes):
        if route.name in HIDDEN_ENDPOINTS:
            continue
        path = full_paths.get(route.operation_id or "", route.path)
        found.append(
            {
                "path": _flask_style(path, route.endpoint),
                "methods": sorted(set(route.methods) - IGNORED_METHODS),
                "endpoint": route.name,
                "view": route.endpoint,
                "path_params": sorted(route.param_convertors),
                "query_params": _fastapi_query_params(route.dependant),
                "required_role": deps.required_role(route),
                "tags": list(route.tags),
            }
        )
    return found


def endpoints(app=None) -> list[dict]:
    """Every route except static files, by section (#623), the menu's order:
    ``section`` is the route's one tag; ``group`` (Pages / API / Monitoring) is
    kept for callers that read it."""
    from app.v1 import sections

    result = []
    for route in routes(app):
        summary, _, description = (inspect.getdoc(route["view"]) or "").partition("\n\n")
        example_body = EXAMPLE_BODIES.get(route["endpoint"])
        result.append(
            {
                "path": route["path"],
                "methods": route["methods"],
                "section": route["tags"][0] if route["tags"] else "",
                "group": _group(route["path"]),
                "summary": _ISSUE_REFS.sub("", " ".join(summary.split())),
                "description": description.strip(),
                "path_params": route["path_params"],
                "query_params": list(
                    dict.fromkeys(
                        [*route["query_params"], *SCOPED_ENDPOINTS.get(route["endpoint"], ())]
                    )
                ),
                "required_role": route["required_role"],
                "example_body": example_body,
                # Pre-formatted for the page's body field, keys in the order above.
                "example_body_text": json.dumps(example_body, indent=2) if example_body else "{}",
            }
        )
    order = {name: index for index, name in enumerate(sections.NAMES)}
    result.sort(
        key=lambda item: (order.get(item["section"], len(order)), item["path"], item["methods"])
    )
    return result


def grouped(app=None) -> list[tuple[str, list[dict]]]:
    items = endpoints(app)
    return [(group, [item for item in items if item["group"] == group]) for group in GROUPS]
