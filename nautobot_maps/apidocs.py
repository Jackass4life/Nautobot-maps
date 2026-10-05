"""The API explorer at /docs (#230): every page and endpoint, and what it takes.

Read from the Flask app's routes, so a new route appears without editing
this module.  Only the example request bodies of write endpoints are listed
by hand; a test fails when a write endpoint has none.
"""

import inspect
import json
import re

from flask import Flask

# Example JSON bodies for the write endpoints, keyed by endpoint name.
EXAMPLE_BODIES = {
    "web.api_set_criticality_override": {
        "nautobot_device_id": "<device uuid>",
        "is_critical": True,
        "reason": "Core uplink for the site",
        "updated_by": "noc",
    },
    "web.mcp_endpoint": {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_alert_board", "arguments": {"filter": "alarms", "limit": 5}},
    },
    "web.api_add_alert_case": {
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


def endpoints(app: Flask) -> list[dict]:
    """Every route except static files, grouped Pages / API / Monitoring."""
    result = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in HIDDEN_ENDPOINTS:
            continue
        view = app.view_functions[rule.endpoint]
        summary, _, description = (inspect.getdoc(view) or "").partition("\n\n")
        example_body = EXAMPLE_BODIES.get(rule.endpoint)
        result.append(
            {
                "path": rule.rule,
                "methods": sorted(rule.methods - IGNORED_METHODS),
                "group": _group(rule.rule),
                "summary": _ISSUE_REFS.sub("", " ".join(summary.split())),
                "description": description.strip(),
                "path_params": sorted(rule.arguments),
                "query_params": _query_params(view),
                "required_role": getattr(view, "required_role", None),
                "example_body": example_body,
                # Pre-formatted for the page's body field, keys in the order above.
                "example_body_text": json.dumps(example_body, indent=2) if example_body else "{}",
            }
        )
    result.sort(key=lambda item: (GROUPS.index(item["group"]), item["path"], item["methods"]))
    return result


def grouped(app: Flask) -> list[tuple[str, list[dict]]]:
    items = endpoints(app)
    return [(group, [item for item in items if item["group"] == group]) for group in GROUPS]
