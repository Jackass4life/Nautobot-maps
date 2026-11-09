"""The API's sections (#623): one per page, in the menu's order, then the ones no
page owns.  Each route carries exactly one section as its OpenAPI tag, so
``/api/docs`` and ``/api/v1/endpoints`` group the API the way the app is used.

A page section takes its name from the menu (``navigation_service.PAGES``), so
the API page and the top bar never disagree.
"""

from dataclasses import dataclass

from app.services import navigation_service as navigation


@dataclass(frozen=True)
class Section:
    key: str
    name: str
    description: str
    # A page of the app (the menu, the status page, Admin), not a group of machines' routes.
    page: bool


_MENU = {key: label for key, _path, label, _title in navigation.PAGES}

# (key, name, description, page): the pages in the menu's order, then the rest.
_S = Section
SECTIONS = (
    _S("map", _MENU["map"], "Sites, a site's detail, alert levels, search, coordinates.", True),
    _S("alert_board", _MENU["alerts"], "Sites in alarm, cases, acks, history, overrides.", True),
    _S("availability", _MENU["availability"], "Per site and tenant, by month; CSV.", True),
    _S("maintenance", _MENU["maintenance"], "Current, planned and past windows; planning.", True),
    _S("circuits", _MENU["circuits"], "Circuits without a termination, their likely site.", True),
    _S("status_page", "Status page", "A tenant's status page, on its private link.", True),
    _S("administration", "Administration", "Tokens, status links, wireless check, contract.", True),
    _S("documentation", _MENU["help"], "The guides, this page, its spec, the endpoint list.", True),
    _S("auth", "Auth", "Who is signed in, and their role.", False),
    _S("incoming_alerts", "Incoming alerts", "Pushed in: LibreNMS, Alertmanager, webhooks.", False),
    _S("shared", "Shared", "What every page reads: settings, the data's freshness.", False),
    _S("monitoring", "Monitoring", "For machines: health, Prometheus metrics, MCP.", False),
    _S("examples", "Examples", "The example resource for new code (items).", False),
)

BY_KEY = {section.key: section for section in SECTIONS}
NAMES = tuple(section.name for section in SECTIONS)


def tag(key: str) -> list[str]:
    """A router's or route's ``tags``: its one section."""
    return [BY_KEY[key].name]


def openapi_tags() -> list[dict]:
    """The spec's ``tags``: every section in order, with its description."""
    return [{"name": section.name, "description": section.description} for section in SECTIONS]
