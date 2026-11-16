"""The pages in the top bar's menu (#392, #419): the API's sections take their names
from it. The menu itself is web/utils/navigation.ts since #737 (tests/test_top_bar.py
checks they agree).

Ported from nautobot_maps/navigation.py (#602): only the imports changed.
"""

# The API reference and its spec, with the API under /api (#483); /docs and
# /openapi.json redirect here.  The reference is a page, not JSON.
API_DOCS_PATH = "/api/docs"
API_SPEC_PATH = "/api/openapi.json"
# Pages under /api: answered and listed as pages, not as the JSON API.
PAGES_UNDER_API = frozenset({API_DOCS_PATH})

# (key, path, label, what it is for).
PAGES = (
    ("map", "/", "Map", "Every site on a map"),
    ("alerts", "/alerts", "Alert Board", "The sites in alarm, cases and history"),
    ("availability", "/availability", "Availability", "Availability per site and tenant, by month"),
    (
        "maintenance",
        "/maintenance",
        "Maintenance",
        "Current and planned maintenance, and planning more",
    ),
    ("circuits", "/circuits", "Circuits", "Circuits without a termination, and their likely site"),
    ("tenants", "/tenants", "Customers", "Each customer's status, as they see it"),
    ("help", "/help", "Documentation", "How it works, setup and every guide"),
    ("docs", API_DOCS_PATH, "API", "Every endpoint, with Try it"),
    ("wall", "/alerts?view=wall", "Wall view", "For a wall screen: alarms only, no controls"),
)
