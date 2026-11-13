"""The permission matrix (#685): every route names what it needs, the old roles keep
exactly the access they had, and every role is refused exactly where the matrix says."""

import pathlib
import re

import pytest
from fastapi.routing import APIRoute

from app.api import app
from app.auth import permissions
from app.auth.permissions import Permission
from app.services import auth_service
from app.services.auth_service import CurrentUser
from app.v1 import deps

# Every route and what it needed before the matrix (main before #685): None for every
# viewer (and anyone, without AUTH_REQUIRE_VIEWER), else the lowest role.
BEFORE = {
    ("/", "GET"): None,
    ("/_nuxt/{path:path}", "GET"): None,
    ("/admin", "GET"): None,
    ("/alerts", "GET"): None,
    ("/api/docs", "GET"): None,
    ("/api/healthz", "GET"): None,
    ("/api/openapi.json", "GET"): None,
    ("/api/v1/alert-acks", "POST"): "operator",
    ("/api/v1/alert-acks/remove", "POST"): "operator",
    ("/api/v1/alert-cases", "POST"): "operator",
    ("/api/v1/alert-feed", "GET"): None,
    ("/api/v1/alert-history", "GET"): "operator",
    ("/api/v1/alert-history.csv", "GET"): "operator",
    ("/api/v1/alertmanager", "POST"): "operator",
    ("/api/v1/alerts", "GET"): None,
    ("/api/v1/app-status", "GET"): None,
    ("/api/v1/availability", "GET"): None,
    ("/api/v1/availability.csv", "GET"): None,
    ("/api/v1/circuit-mapping", "GET"): None,
    ("/api/v1/circuit-mapping.csv", "GET"): None,
    ("/api/v1/contract", "GET"): None,
    ("/api/v1/criticality-overrides", "GET"): "operator",
    ("/api/v1/criticality-overrides", "POST"): "operator",
    ("/api/v1/criticality-overrides/{device_id}", "DELETE"): "operator",
    ("/api/v1/device-status", "POST"): "operator",
    ("/api/v1/endpoints", "GET"): None,
    ("/api/v1/help/{slug}", "GET"): None,
    ("/api/v1/items", "GET"): None,
    ("/api/v1/items", "POST"): "operator",
    ("/api/v1/items/{item_id}", "DELETE"): "operator",
    ("/api/v1/items/{item_id}", "GET"): None,
    ("/api/v1/librenms/alert", "POST"): "operator",
    ("/api/v1/location-alerts", "GET"): None,
    ("/api/v1/locations", "GET"): None,
    ("/api/v1/locations/coordinate-check", "GET"): None,
    ("/api/v1/locations/coordinate-check.csv", "GET"): None,
    ("/api/v1/locations/{location_id}/detail", "GET"): None,
    ("/api/v1/maintenance", "GET"): None,
    ("/api/v1/maintenance", "POST"): "operator",
    ("/api/v1/maintenance/devices", "GET"): None,
    ("/api/v1/maintenance/end", "POST"): "operator",
    ("/api/v1/maintenance/history", "GET"): None,
    ("/api/v1/maintenance/history.csv", "GET"): None,
    ("/api/v1/maintenance/sites", "GET"): None,
    ("/api/v1/maintenance/{window_id:int}/end", "POST"): "operator",
    ("/api/v1/me", "GET"): None,
    ("/api/v1/notifications/test", "POST"): "admin",
    ("/api/v1/page-config", "GET"): None,
    ("/api/v1/search", "GET"): None,
    ("/api/v1/status-links/{tenant:path}", "GET"): "admin",
    ("/api/v1/status-links/{tenant:path}/replace", "POST"): "admin",
    ("/api/v1/status/{link_id}", "GET"): None,
    ("/api/v1/tenants", "GET"): "admin",
    ("/api/v1/tokens", "GET"): "admin",
    ("/api/v1/tokens", "POST"): "admin",
    ("/api/v1/tokens/purge", "POST"): "admin",
    ("/api/v1/tokens/{token_id}/revoke", "POST"): "admin",
    ("/api/v1/wireless/check", "POST"): "admin",
    ("/availability", "GET"): None,
    ("/circuits", "GET"): None,
    ("/docs", "GET"): None,
    ("/healthz", "GET"): None,
    ("/help", "GET"): None,
    ("/help/images/{name:path}", "GET"): None,
    ("/help/{slug}", "GET"): None,
    ("/maintenance", "GET"): None,
    ("/mcp", "POST"): None,
    ("/metrics", "GET"): None,
    ("/openapi.json", "GET"): None,
    ("/static/{path:path}", "GET"): None,
    ("/status/{link_id}", "GET"): None,
}
# Deliberate changes since, with their issue: what each route needs now.
CHANGED = {
    # #687: NWO (operator) keeps the status links, and the status tokens sent with
    # them; reading a link no longer makes one.
    ("/api/v1/status-links", "GET"): "operator",
    ("/api/v1/status-links/{tenant:path}", "GET"): "operator",
    ("/api/v1/status-links/{tenant:path}", "POST"): "operator",
    ("/api/v1/status-links/{tenant:path}", "DELETE"): "operator",
    ("/api/v1/status-links/{tenant:path}/replace", "POST"): "operator",
    # Status tokens only, without admin (the route checks the role).
    ("/api/v1/tokens", "GET"): "operator",
    ("/api/v1/tokens", "POST"): "operator",
    ("/api/v1/tokens/{token_id}/revoke", "POST"): "operator",
    # #689: a tenant's status in the app, by its name (tenant.status.view: viewer and up).
    ("/api/v1/tenants/{tenant:path}/status", "GET"): None,
    # #695: its page in the app.
    ("/tenants/{tenant:path}", "GET"): None,
    # #697: every tenant (the Customers page reads the list), and the roles'
    # workspaces: pages, reads.
    ("/tenants", "GET"): None,
    ("/api/v1/tenants", "GET"): None,
    **{
        (prefix + path + suffix, "GET"): None
        for prefix, paths in permissions.WORKSPACES.values()
        for page, path in paths.items()
        for suffix in ("", "/{tenant:path}")
        if not suffix or page == "tenants"
    },
}
NOW = BEFORE | CHANGED
LEVEL = {"viewer": 1, "operator": 2, "admin": 3}
ANONYMOUS = "anonymous"


def _routes():  # type: ignore[no-untyped-def]
    def every(routes):  # type: ignore[no-untyped-def]
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from every(original.routes)
            elif isinstance(route, APIRoute):
                yield route

    # The new stack's routers' routes carry their path without /api/v1 (FastAPI keeps
    # included routers as they are): their full path from the spec, by operation id.
    full = {
        operation["operationId"]: path
        for path, operations in app.openapi()["paths"].items()
        for operation in operations.values()
    }
    for route in every(app.routes):
        spec_path = full.get(route.operation_id or "", "")
        path = spec_path if spec_path.endswith(route.path) else route.path
        for method in sorted(route.methods - {"HEAD"}):
            yield path, route, method


ROUTES = list(_routes())


def _route_id(item) -> str:  # type: ignore[no-untyped-def]
    path, _route, method = item
    return f"{method} {path}"


def allowed(route: APIRoute, role: str) -> bool:
    """What the matrix says: every permission group of *route* met by *role*."""
    has = permissions.role_permissions("viewer" if role == ANONYMOUS else role)
    return all(
        group == (permissions.PUBLIC,) or any(Permission(name) in has for name in group)
        for group in deps.required_permissions(route)
    )


def test_every_route_names_what_it_needs():
    """A route without a permission (or the public marker) fails here: nothing is open
    by accident."""
    unnamed = [_route_id(item) for item in ROUTES if not deps.required_permissions(item[1])]
    assert unnamed == []
    assert {(path, method) for path, _route, method in ROUTES} == set(NOW), (
        "a route was added or removed: give it a permission and add it to CHANGED"
    )


def test_operator_is_everything_but_admin():
    assert permissions.role_permissions("operator") == permissions.ALL - {Permission.ADMIN}
    assert permissions.role_permissions("admin") == permissions.ALL


@pytest.mark.parametrize("item", ROUTES, ids=_route_id)
def test_the_old_roles_keep_their_access(item):
    """viewer, operator and admin reach exactly what they reached before (or since a
    deliberate change, CHANGED), and so does anyone without AUTH_REQUIRE_VIEWER (the
    viewer's reads, #592)."""
    path, route, method = item
    before = NOW[(path, method)]
    for role in ("viewer", "operator", "admin"):
        assert allowed(route, role) == (before is None or LEVEL[role] >= LEVEL[before]), role
    assert allowed(route, ANONYMOUS) == (before is None), ANONYMOUS


# Path parameters, filled in for a real request.
FILL = {
    "path": "x.js",
    "tenant": "Acme",
    "location_id": "loc-x",
    "device_id": "dev-x",
    "window_id": "1",
    "item_id": "1",
    "token_id": "1",
    "link_id": "nolink",
    "slug": "overview",
    "name": "x.png",
}


def _url(path: str) -> str:
    return re.sub(r"\{(\w+)(?::[^}]+)?\}", lambda m: FILL[m.group(1)], path)


def _refused(response) -> bool:  # type: ignore[no-untyped-def]
    if response.status_code not in (401, 403):
        return False
    try:
        error = response.json().get("error")
    except ValueError:
        return False
    return error in ("Authentication required", "Insufficient permissions")


@pytest.fixture
def as_role(configure, monkeypatch):  # type: ignore[no-untyped-def]
    """Requests as *role* (header mode, AUTH_REQUIRE_VIEWER), or as nobody without it."""

    def apply(role: str) -> None:
        configure(AUTH_MODE="header", AUTH_REQUIRE_VIEWER=str(role != ANONYMOUS).lower())
        if role == ANONYMOUS:
            user = CurrentUser(auth_mode="header")
        else:
            user = CurrentUser(is_authenticated=True, username=role, role=role, auth_mode="header")

        async def resolve(request, settings):  # type: ignore[no-untyped-def]
            return user

        monkeypatch.setattr(auth_service, "resolve_user", resolve)

    return apply


@pytest.mark.parametrize("role", [*permissions.ROLES, ANONYMOUS])
def test_every_role_is_refused_exactly_where_the_matrix_says(role, as_role, client):
    """Every route called as *role*: refused (401/403 for the permission) exactly where
    the matrix says, never by accident elsewhere."""
    as_role(role)
    wrong = []
    for path, route, method in ROUTES:
        response = client.request(method, _url(path))
        if _refused(response) == allowed(route, role):
            wrong.append(f"{method} {path}: {response.status_code}")
    assert wrong == []


def test_a_refusal_names_the_permission(as_role, client):
    as_role("dov")
    refused = client.post("/api/v1/criticality-overrides", json={})
    assert refused.status_code == 403
    assert refused.json() == {
        "error": "Insufficient permissions",
        "required_permission": "criticality.override",
        "required_role": "operator",
        "current_role": "dov",
    }


# What the new roles reach, written out from the design (#685), not from the routes'
# declarations: a declaration that drifts fails here. Every role reaches the basics.
BASICS = {
    "GET /_nuxt/{path:path}",
    "GET /api/docs",
    "GET /api/healthz",
    "GET /api/openapi.json",
    "GET /api/v1/app-status",
    "GET /api/v1/contract",
    "GET /api/v1/endpoints",
    "GET /api/v1/help/{slug}",
    "GET /api/v1/items",
    "GET /api/v1/items/{item_id}",
    "GET /api/v1/me",
    "GET /api/v1/page-config",
    "GET /api/v1/status/{link_id}",
    "GET /docs",
    "GET /healthz",
    "GET /help",
    "GET /help/images/{name:path}",
    "GET /help/{slug}",
    "GET /metrics",
    "GET /openapi.json",
    "GET /static/{path:path}",
    "GET /status/{link_id}",
    "POST /mcp",
    # The front door (#697): its page sends each role to its landing page.
    "GET /",
    # The Admin page's shell, as for a viewer: every administration route refuses.
    # The workspaces (PR 7) send these roles elsewhere.
    "GET /admin",
}
REACHES = {
    "customer": BASICS
    | {
        "GET /availability",
        "GET /api/v1/availability",
        "GET /api/v1/availability.csv",
        "GET /api/v1/tenants/{tenant:path}/status",
        "GET /tenants/{tenant:path}",
        # #697: its workspace, and the same pages in the others' (the pages send it
        # to its own).
        "GET /tenants",
        "GET /api/v1/tenants",
        "GET /customer",
        "GET /customer/tenants",
        "GET /customer/tenants/{tenant:path}",
        "GET /dov/availability",
        "GET /nwo/availability",
        "GET /nwo/tenants",
        "GET /nwo/tenants/{tenant:path}",
    },
    "dov": BASICS
    | {
        "GET /alerts",
        "GET /api/v1/alerts",
        "GET /api/v1/alert-feed",
        "GET /api/v1/alert-history",
        "GET /api/v1/alert-history.csv",
        "POST /api/v1/alert-acks",
        "POST /api/v1/alert-acks/remove",
        "POST /api/v1/alert-cases",
        # The top bar's site search, whose results open on the board.
        "GET /api/v1/locations",
        "GET /availability",
        "GET /api/v1/availability",
        "GET /api/v1/availability.csv",
        "GET /maintenance",
        "GET /api/v1/maintenance",
        "GET /api/v1/maintenance/devices",
        "GET /api/v1/maintenance/sites",
        "GET /api/v1/maintenance/history",
        "GET /api/v1/maintenance/history.csv",
        # #697: its workspace, and the same pages in the others'.
        "GET /dov",
        "GET /dov/board",
        "GET /dov/availability",
        "GET /dov/maintenance",
        "GET /customer",
        "GET /nwo/wall",
        "GET /nwo/board",
        "GET /nwo/availability",
        "GET /nwo/maintenance",
    },
}


@pytest.mark.parametrize("role", sorted(REACHES))
def test_the_new_roles_reach_what_the_design_gives_them(role):
    reached = {_route_id(item) for item in ROUTES if allowed(item[1], role)}
    assert reached == REACHES[role]


# --- What the pages are told (#693) -------------------------------------------------

MODES = {
    "header": {"AUTH_MODE": "header", "AUTH_REQUIRE_VIEWER": "true"},
    "header, open reads": {"AUTH_MODE": "header", "AUTH_REQUIRE_VIEWER": "false"},
    "disabled": {"AUTH_MODE": "disabled", "ALLOW_UNAUTHENTICATED_WRITES": "false"},
    "disabled, open writes": {"AUTH_MODE": "disabled", "ALLOW_UNAUTHENTICATED_WRITES": "true"},
}


def _callers():  # type: ignore[no-untyped-def]
    for mode in MODES:
        yield mode, None
        if mode.startswith("header"):
            yield from ((mode, role) for role in permissions.ROLES)


@pytest.mark.parametrize(("mode", "role"), list(_callers()))
def test_the_pages_are_told_exactly_what_the_api_allows(configure, mode, role):
    """access.permissions (#693) against the real check, route by route: a change is
    allowed by permission_problem exactly when its permissions are in the set, so a page
    never shows a button the API refuses, nor hides one it allows; a read is allowed
    whenever they are. (With AUTH_MODE=disabled every read is open, also a feature's
    whose changes aren't, e.g. the criticality list: the set names what can be used.)"""
    from app.core.settings import get_settings

    configure(**MODES[mode])
    settings = get_settings()
    if role is None:
        user = CurrentUser(auth_mode=settings.auth_mode)
    else:
        user = CurrentUser(is_authenticated=True, username=role, role=role, auth_mode="header")
    have = set(auth_service.access(user, settings)["permissions"])
    wrong = []
    for path, route, method in ROUTES:
        for dependency in route.dependant.dependencies:
            names = getattr(dependency.call, "required_permissions", None)
            if not names or names == (permissions.PUBLIC,):
                continue
            checked = auth_service.permission_problem(
                user,
                settings,
                tuple(Permission(n) for n in names),
                method,
                getattr(dependency.call, "open_when_disabled", False),
            )
            told = bool(set(names) & have)
            if (checked is None) != told and (method != "GET" or told):
                wrong.append(f"{method} {path} {names}")
    assert wrong == []


ALL_PAGES = ["map", "alerts", "wall", "availability", "maintenance", "circuits", "help", "docs"]


@pytest.mark.parametrize(
    ("role", "pages"),
    [
        (
            "admin",
            [
                "map",
                "alerts",
                "wall",
                "availability",
                "maintenance",
                "circuits",
                "tenants",
                "help",
                "docs",
            ],
        ),
        (
            "operator",
            [
                "map",
                "alerts",
                "wall",
                "availability",
                "maintenance",
                "circuits",
                "tenants",
                "help",
                "docs",
            ],
        ),
        (
            "viewer",
            [
                "map",
                "alerts",
                "wall",
                "availability",
                "maintenance",
                "circuits",
                "tenants",
                "help",
                "docs",
            ],
        ),
        ("dov", ["alerts", "wall", "availability", "maintenance", "help", "docs"]),
        ("customer", ["availability", "tenants", "help", "docs"]),
    ],
)
def test_the_menu_by_role(role, pages, as_role, client):
    as_role(role)
    config = client.get("/api/v1/page-config").json()
    assert config["access"]["role"] == role
    assert config["access"]["pages"] == pages
    assert [page["key"] for page in config["pages"]] == [
        key
        for key, *_ in __import__("app.services.navigation_service", fromlist=["PAGES"]).PAGES
        if key in pages
    ]
    assert config["access"]["landing"] == permissions.ROLES[role].landing


@pytest.mark.parametrize(
    ("role", "hrefs"),
    [
        ("customer", ["/customer", "/customer/tenants", "/help", "/api/docs"]),
        (
            "dov",
            ["/dov/board", "/dov/availability", "/dov/maintenance", "/help", "/api/docs", "/dov"],
        ),
        (
            "operator",
            [
                "/nwo",
                "/nwo/board",
                "/nwo/availability",
                "/nwo/maintenance",
                "/nwo/circuits",
                "/nwo/tenants",
                "/help",
                "/api/docs",
                "/nwo/wall",
            ],
        ),
        (
            "viewer",
            [
                "/",
                "/alerts",
                "/availability",
                "/maintenance",
                "/circuits",
                "/tenants",
                "/help",
                "/api/docs",
                "/alerts?view=wall",
            ],
        ),
    ],
)
def test_the_menu_opens_the_roles_workspace(role, hrefs, as_role, client):
    """Customer, DOV and NWO each have their own addresses (#697); the others the flat
    ones. Each role lands on its workspace."""
    as_role(role)
    config = client.get("/api/v1/page-config").json()
    assert [page["href"] for page in config["pages"]] == hrefs
    landing = config["access"]["landing"]
    assert landing == {"customer": "/customer", "dov": "/dov", "operator": "/nwo"}.get(role, "/")


def test_the_web_knows_the_workspaces():
    """web/utils/workspaces.ts holds WORKSPACES too (#697): its routes and redirects."""
    import json

    source = (
        pathlib.Path(__file__).resolve().parents[2] / "web" / "utils" / "workspaces.ts"
    ).read_text()
    table = source.split("export const WORKSPACES = ", 1)[1].split(" as const", 1)[0]
    # TypeScript to JSON: quoted keys, no trailing commas.
    table = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', table)
    table = re.sub(r",(\s*[}\]])", r"\1", table)
    assert json.loads(table) == {
        role: {"prefix": prefix, "pages": paths}
        for role, (prefix, paths) in permissions.WORKSPACES.items()
    }


def test_every_menu_page_needs_a_permission():
    from app.services import navigation_service as navigation

    assert {key for key, *_ in navigation.PAGES} == set(permissions.PAGE_PERMISSIONS)


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("role", [None, "viewer", "operator", "admin"])
def test_can_write_and_can_administer_as_before(configure, mode, role):
    """The deprecated flags (#693) answer as before for the old roles: can_write the
    board's own changes, can_administer a POST needing admin."""
    from app.core.settings import get_settings

    if role and not mode.startswith("header"):
        return
    configure(**MODES[mode])
    settings = get_settings()
    user = (
        CurrentUser(auth_mode=settings.auth_mode)
        if role is None
        else CurrentUser(is_authenticated=True, username=role, role=role, auth_mode="header")
    )
    disabled = settings.auth_mode == "disabled"
    writes_open = settings.allow_unauthenticated_writes
    was_write = (disabled or role in ("operator", "admin")) and (role is not None or disabled)
    was_admin = (disabled and writes_open) or role == "admin"
    assert auth_service.may_change_the_board(user, settings) == was_write
    assert auth_service.may_administer(user, settings) == was_admin


def test_the_web_knows_every_permission():
    """web/composables/useCan.ts names the matrix's permissions (#695): a renamed or
    new one there too, or a button would never show."""
    source = (
        pathlib.Path(__file__).resolve().parents[2] / "web" / "composables" / "useCan.ts"
    ).read_text()
    union = source.split("export type Permission =", 1)[1].split(";", 1)[0]
    assert set(re.findall(r'"([a-z.]+)"', union)) == {str(p) for p in Permission}
