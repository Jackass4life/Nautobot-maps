"""The pages in the top bar's menu (#392, #419), which the pages (Nuxt, #463)
read from ``/api/page-config``."""

# The API reference and its spec, with the API under /api (#483); /docs and
# /openapi.json redirect here.  The reference is a page, not JSON.
API_DOCS_PATH = "/api/docs"
API_SPEC_PATH = "/api/openapi.json"
# Pages under /api: answered and listed as pages, not as the JSON API.
PAGES_UNDER_API = frozenset({API_DOCS_PATH})

# (key, path, label, what it is for).  The map, the board and the wall keep
# the page's ?tenant= / ?q= (#366).
PAGES = (
    ("map", "/", "Map", "Every site on a map"),
    ("alerts", "/alerts", "Alert Board", "The sites in alarm, cases and history"),
    ("availability", "/availability", "Availability", "Availability per site and tenant, by month"),
    ("maintenance", "/maintenance", "Maintenance", "Current and planned maintenance, and planning more"),
    ("circuits", "/circuits", "Circuits", "Circuits without a termination, and their likely site"),
    ("help", "/help", "Documentation", "How it works, setup and every guide"),
    ("docs", API_DOCS_PATH, "API", "Every endpoint, with Try it"),
    ("wall", "/alerts?view=wall", "Wall view", "For a wall screen: alarms only, no controls"),
)
SCOPED = {"map", "alerts", "wall"}


def pages(scope_query: str = "") -> list[dict]:
    """The menu's pages; *scope_query* (``tenant=a&q=b``, the page's ?tenant= / ?q=)
    kept on the scoped ones."""
    result = []
    for key, path, label, title in PAGES:
        href = path
        if scope_query and key in SCOPED:
            href = f"{path}{'&' if '?' in path else '?'}{scope_query}"
        result.append({"key": key, "href": href, "label": label, "title": title})
    return result
