"""Who may do what (#685): one matrix, the only place a role's rights are written.

Every API route names the permission it needs (``deps.require_permission``); a role
has a set of them. The API is the only real security: the pages hide what a role
can't use, from the same matrix, for convenience only.

Roles:
- ``admin``: everything.
- ``operator`` (NWO): everything but ``admin``, so a new permission reaches it at once.
- ``viewer``: every page it could read before the matrix (#592), no changes.
- ``noc``: the wall, the board, availability and maintenance (read only); it
  acknowledges and adds cases.
- ``customer``: availability and each tenant's status, read only.
- ``status``: an API token for one tenant's status page; no permission here, its
  access is the status page's own (``auth_service.status_access``).
"""

from dataclasses import dataclass
from enum import StrEnum


class Permission(StrEnum):
    # Pages, and the data they read.
    PAGE_MAP = "page.map"
    PAGE_BOARD = "page.board"
    PAGE_WALL = "page.wall"
    PAGE_AVAILABILITY = "page.availability"
    PAGE_CIRCUITS = "page.circuits"
    PAGE_MAINTENANCE = "page.maintenance"
    ALERTS_HISTORY = "alerts.history"
    TENANT_STATUS_VIEW = "tenant.status.view"
    STATUS_LINKS_VIEW = "status.links.view"
    EXPORT_CSV = "export.csv"
    # Changes.
    ALERTS_ACK = "alerts.ack"
    ALERTS_UNACK = "alerts.unack"
    ALERTS_CASE = "alerts.case"
    CRITICALITY_OVERRIDE = "criticality.override"
    MAINTENANCE_WRITE = "maintenance.write"
    STATUS_LINKS_MANAGE = "status.links.manage"
    # Integrations' pushes: Alertmanager, device status, LibreNMS (API tokens).
    ALERTS_INGEST = "alerts.ingest"
    # The examples section's items (#572).
    EXAMPLES_WRITE = "examples.write"
    ADMIN = "admin"
    # What every signed-in role may read: the top bar, the help pages, the API's
    # description, MCP (whose tools are checked call by call).
    SESSION_BASIC = "session.basic"


# Routes anyone reaches without signing in (health, metrics, the status page): a
# marker, not a permission any role holds.
PUBLIC = "public"

ALL = frozenset(Permission)


@dataclass(frozen=True)
class RoleDef:
    label: str
    # The page a role opens on.
    landing: str
    permissions: frozenset[Permission]


P = Permission

VIEWER = frozenset(
    {
        P.PAGE_MAP,
        P.PAGE_BOARD,
        P.PAGE_WALL,
        P.PAGE_AVAILABILITY,
        P.PAGE_CIRCUITS,
        P.PAGE_MAINTENANCE,
        P.TENANT_STATUS_VIEW,
        P.EXPORT_CSV,
        P.SESSION_BASIC,
    }
)

ROLES: dict[str, RoleDef] = {
    "admin": RoleDef("Admin", "/", ALL),
    "operator": RoleDef("NWO (operator)", "/nwo", ALL - {P.ADMIN}),
    "viewer": RoleDef("Viewer", "/", VIEWER),
    "noc": RoleDef(
        "NOC",
        "/noc",
        frozenset(
            {
                P.PAGE_WALL,
                P.PAGE_BOARD,
                P.PAGE_AVAILABILITY,
                P.PAGE_MAINTENANCE,
                P.ALERTS_HISTORY,
                P.ALERTS_ACK,
                P.ALERTS_UNACK,
                P.ALERTS_CASE,
                P.EXPORT_CSV,
                P.SESSION_BASIC,
            }
        ),
    ),
    "customer": RoleDef(
        "Customer",
        "/customer",
        frozenset({P.PAGE_AVAILABILITY, P.TENANT_STATUS_VIEW, P.EXPORT_CSV, P.SESSION_BASIC}),
    ),
}

# What reads ask for (every route of theirs is a GET): with AUTH_MODE=disabled anyone
# has them, as every read is open then (#188).
READS = frozenset(
    {
        P.PAGE_MAP,
        P.PAGE_BOARD,
        P.PAGE_WALL,
        P.PAGE_AVAILABILITY,
        P.PAGE_CIRCUITS,
        P.PAGE_MAINTENANCE,
        P.ALERTS_HISTORY,
        P.TENANT_STATUS_VIEW,
        P.STATUS_LINKS_VIEW,
        P.EXPORT_CSV,
        P.SESSION_BASIC,
    }
)
# The board's own changes, open with AUTH_MODE=disabled (their routes'
# ``open_when_disabled``), and the examples section's.
OPEN_WHEN_DISABLED = frozenset({P.ALERTS_ACK, P.ALERTS_UNACK, P.ALERTS_CASE, P.EXAMPLES_WRITE})

# Each page in the top bar's menu (navigation_service.PAGES' keys, #693) and the
# permission it needs.
PAGE_PERMISSIONS: dict[str, Permission] = {
    "map": P.PAGE_MAP,
    "alerts": P.PAGE_BOARD,
    "wall": P.PAGE_WALL,
    "availability": P.PAGE_AVAILABILITY,
    "maintenance": P.PAGE_MAINTENANCE,
    "circuits": P.PAGE_CIRCUITS,
    "tenants": P.TENANT_STATUS_VIEW,
    "help": P.SESSION_BASIC,
    "docs": P.SESSION_BASIC,
}

# Each role's own addresses (#697): its workspace, and where each of its pages is in
# it ("" = the workspace itself, the role's landing). Viewer and admin keep the flat
# addresses. A tenant's status is at <workspace>/tenants/<name> with "tenants".
# web/utils/workspaces.ts holds the same table (tests/test_permissions.py checks).
WORKSPACES: dict[str, tuple[str, dict[str, str]]] = {
    "customer": ("/customer", {"availability": "", "tenants": "/tenants"}),
    "noc": (
        "/noc",
        {
            "wall": "",
            "alerts": "/board",
            "availability": "/availability",
            "maintenance": "/maintenance",
        },
    ),
    "operator": (
        "/nwo",
        {
            "map": "",
            "wall": "/wall",
            "alerts": "/board",
            "availability": "/availability",
            "tenants": "/tenants",
            "maintenance": "/maintenance",
            "circuits": "/circuits",
        },
    ),
}


def workspace_path(role: str | None, page: str) -> str | None:
    """*page*'s address in *role*'s workspace, or None (no workspace, or not in it)."""
    found = WORKSPACES.get(role or "")
    if not found or page not in found[1]:
        return None
    prefix, paths = found
    return prefix + paths[page]


# The roles before the matrix, lowest first: refusals still name the lowest of them
# that would do (scripts and MCP read ``required_role``).
OLD_ROLES = ("viewer", "operator", "admin")


def role_permissions(role: str) -> frozenset[Permission]:
    """*role*'s permissions; none for an unknown role (or a status token)."""
    found = ROLES.get((role or "").strip().lower())
    return found.permissions if found else frozenset()


def lowest_old_role(permission: Permission) -> str | None:
    """The lowest of viewer, operator, admin holding *permission*, or None."""
    return next((role for role in OLD_ROLES if permission in ROLES[role].permissions), None)
