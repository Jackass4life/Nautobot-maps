"""The API explorer at /docs (#230): every page and endpoint, and what it takes.

Read from the Flask app's routes, so a new route appears without editing
this module.  Only the example request bodies of write endpoints are listed
by hand; a test fails when a write endpoint has none.
"""

import inspect
import json
import re

from flask import Flask

from nautobot_maps import scope

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
    "ingest.api_device_status": {"device": "sw1.example.com", "status": "down", "source": "zabbix", "id": "123"},
    "admin.api_tokens_create": {"name": "librenms", "role": "operator", "expires_in_days": 365},
    "admin.api_tokens_revoke": {},
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
HIDDEN_ENDPOINTS = {"static"}
IGNORED_METHODS = {"HEAD", "OPTIONS"}
# request.args.get("limit") / request.args.getlist("kinds") in a view's source.
_QUERY_ARG = re.compile(r"request\.args\.get(?:list)?\(\s*[\"']([A-Za-z0-9_]+)[\"']")
# " (#180)" / " (#227, #228)": issue references, for developers, not readers.
_ISSUE_REFS = re.compile(r"\s*\(#\d+(?:,\s*#\d+)*\)")


def _group(path: str) -> str:
    if path in MONITORING_PATHS:
        return "Monitoring"
    return "API" if path.startswith("/api/") or path == "/mcp" else "Pages"


def _query_params(view) -> list[str]:
    try:
        source = inspect.getsource(inspect.unwrap(view))
    except (OSError, TypeError):
        return []
    return list(dict.fromkeys(_QUERY_ARG.findall(source)))


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


def routes(app: Flask | None = None) -> list[dict]:
    """Every route the app serves, on FastAPI or still on Flask (#436):
    ``path`` (Flask's notation), ``methods``, ``endpoint``, ``view``,
    ``path_params``, ``query_params``, ``required_role``."""
    if app is None:
        import app as flask_module

        app = flask_module.app
    found = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in HIDDEN_ENDPOINTS:
            continue
        view = app.view_functions[rule.endpoint]
        found.append(
            {
                "path": rule.rule,
                "methods": sorted(rule.methods - IGNORED_METHODS),
                "endpoint": rule.endpoint,
                "view": view,
                "path_params": sorted(rule.arguments),
                "query_params": _query_params(view),
                "required_role": getattr(view, "required_role", None),
            }
        )
    from fastapi.routing import APIRoute

    import nautobot_maps.api
    from nautobot_maps.api import deps

    # From our routers (no prefixes), not the app's route list: FastAPI keeps
    # an included router there as one private object.
    for route in (route for router in nautobot_maps.api.ROUTERS for route in router.routes):
        if not isinstance(route, APIRoute):
            continue
        found.append(
            {
                "path": _flask_style(route.path, route.endpoint),
                "methods": sorted(set(route.methods) - IGNORED_METHODS),
                "endpoint": route.name,
                "view": route.endpoint,
                "path_params": sorted(route.param_convertors),
                "query_params": [param.alias for param in route.dependant.query_params],
                "required_role": deps.required_role(route),
            }
        )
    return found


def endpoints(app: Flask | None = None) -> list[dict]:
    """Every route except static files, grouped Pages / API / Monitoring."""
    result = []
    for route in routes(app):
        summary, _, description = (inspect.getdoc(route["view"]) or "").partition("\n\n")
        example_body = EXAMPLE_BODIES.get(route["endpoint"])
        result.append(
            {
                "path": route["path"],
                "methods": route["methods"],
                "group": _group(route["path"]),
                "summary": _ISSUE_REFS.sub("", " ".join(summary.split())),
                "description": description.strip(),
                "path_params": route["path_params"],
                "query_params": list(
                    dict.fromkeys([*route["query_params"], *SCOPED_ENDPOINTS.get(route["endpoint"], ())])
                ),
                "required_role": route["required_role"],
                "example_body": example_body,
                # Pre-formatted for the page's body field, keys in the order above.
                "example_body_text": json.dumps(example_body, indent=2) if example_body else "{}",
            }
        )
    result.sort(key=lambda item: (GROUPS.index(item["group"]), item["path"], item["methods"]))
    return result


def grouped(app: Flask | None = None) -> list[tuple[str, list[dict]]]:
    items = endpoints(app)
    return [(group, [item for item in items if item["group"] == group]) for group in GROUPS]
