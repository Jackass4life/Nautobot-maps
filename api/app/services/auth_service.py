"""Who is calling, and what they may do: the current app's rules (nautobot_maps/auth.py, #592).

- ``AUTH_MODE=header``: a trusted sign-in proxy sets the user and group headers;
  they count only when the direct peer is in AUTH_TRUSTED_PROXIES (and sends
  AUTH_PROXY_SECRET when one is set). The role comes from the groups.
- ``AUTH_MODE=disabled``: nobody is signed in. Reads, and the changes the board
  itself makes (``open_when_disabled``), are open; other changes need
  ALLOW_UNAUTHENTICATED_WRITES (#188).
- An API token (``Authorization: Bearer nmt_...``, #297) signs in as
  ``token:<name>`` with the token's role, in either mode and from any address.

Refusals are ``(body, status)``: ``{"error": ...}`` as the current app answers them.
"""

import hmac
import ipaddress
import re
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from fastapi import Request
from loguru import logger

from app.auth import permissions
from app.auth import scopes as scope_rules
from app.auth.permissions import Permission
from app.core.exceptions import NO_ACCESS
from app.core.settings import Settings
from app.db import session
from app.services import ad_service, identity_service, login_service, token_service

# The old roles' order (#592): the role groups give (``role_from_groups``) and the role
# names a token may have. What a role may do is the matrix's (app/auth/permissions.py).
ROLE_LEVELS: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}
PROXY_SECRET_HEADER = "X-Auth-Proxy-Secret"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Reachable without the viewer role even with AUTH_REQUIRE_VIEWER: probes and
# Prometheus scrapes send no identity headers; /metrics has only counts (#200).
PUBLIC_PATHS = {"/healthz", "/metrics", "/api/healthz"}
SIGN_IN_PATH = "/auth"
API_KEY_HEADER = "X-API-KEY"
SIGN_IN_PATHS = {SIGN_IN_PATH, "/api/v1/auth/login", "/api/v1/auth/logout"}
# A tenant's status page (#291): the page and its assets carry no data, so a
# customer's browser loads them without SSO; the status API checks access itself
# (``status_access``). /_nuxt/ is the app's code only, no data.
STATUS_PAGE_PREFIX = "/status/"
STATUS_API_PREFIX = "/api/v1/status/"
FRONTEND_ASSETS_PREFIX = "/_nuxt/"
MCP_PATH = "/mcp"

Refusal = tuple[dict, int]

# Peers already warned about, so a scanner can't flood the log.
_logged_untrusted: set[str] = set()


@dataclass(frozen=True)
class CurrentUser:
    is_authenticated: bool = False
    username: str = ""
    groups: list[str] = field(default_factory=list)
    role: str = ""
    auth_mode: str = "disabled"
    # An API token's name, and a status token's tenant (#291).
    token: str = ""
    tenant: str = ""
    # A Bearer token of ours that isn't active (unknown, revoked, expired).
    token_rejected: bool = False
    # From an identity (#715): every role (the highest first, ``role``), own scopes,
    # its name. ``no_identity``: signed in without one, with AUTH_GROUP_ROLES=false.
    roles: tuple[str, ...] = ()
    scopes: frozenset[str] = field(default_factory=frozenset)
    identity: str = ""
    common_name: str = ""
    no_identity: bool = False
    # How they signed in: "proxy", "app" (the app's own sign-in, #717) or "token".
    signed_in_with: str = ""


def role_level(role: str) -> int:
    return ROLE_LEVELS.get((role or "").strip().lower(), 0)


def group_roles(settings: Settings) -> list[tuple[str, frozenset[str]]]:
    """Each role groups can give and its groups, first wins (#691): admin, operator
    (NWO), noc, customer, viewer."""
    return [
        ("admin", settings.AUTH_ADMIN_GROUPS),
        ("operator", settings.AUTH_OPERATOR_GROUPS),
        ("noc", noc_groups(settings)),
        ("customer", settings.AUTH_CUSTOMER_GROUPS),
        ("viewer", settings.AUTH_VIEWER_GROUPS),
    ]


def noc_groups(settings: Settings) -> frozenset[str]:
    """AUTH_NOC_GROUPS, or the old AUTH_DOV_GROUPS when it is empty (#703)."""
    if settings.AUTH_NOC_GROUPS or not settings.AUTH_DOV_GROUPS:
        return settings.AUTH_NOC_GROUPS
    global _dov_warned
    if not _dov_warned:
        _dov_warned = True
        logger.warning("AUTH_DOV_GROUPS is deprecated: rename it AUTH_NOC_GROUPS (#703)")
    return settings.AUTH_DOV_GROUPS


_dov_warned = False


def role_from_groups(groups: list[str], settings: Settings) -> str:
    """One role for a user's groups: the first in ``group_roles`` with one of them,
    else AUTH_DEFAULT_ROLE. Roles are never merged."""
    names = {group.strip().lower() for group in groups if group.strip()}
    for role, role_groups in group_roles(settings):
        if names & role_groups:
            return role
    return settings.AUTH_DEFAULT_ROLE


def bearer_token(request: Request) -> str:
    """The API token in ``Authorization: Bearer``, or "" (also for a Bearer value
    that isn't ours, e.g. a proxy's own access token)."""
    scheme, _, value = request.headers.get("authorization", "").strip().partition(" ")
    value = value.strip()
    if scheme.lower() == "bearer" and token_service.is_token(value):
        return value
    # The example's header (#719).
    key = request.headers.get(API_KEY_HEADER, "").strip()
    return key if token_service.is_token(key) else ""


async def _verify_token(token: str) -> dict | None:
    """The token's owner, on a session of its own: last_used_at is committed
    whatever the request does."""
    async with session.AsyncSessionLocal() as own:
        return await token_service.verify(own, token)


async def _token_user(token: str, settings: Settings) -> CurrentUser:
    found = None
    try:
        found = await _verify_token(token)
    except Exception as exc:  # noqa: BLE001 - the database; never log the token
        logger.warning("API token check failed: {}", type(exc).__name__)
    if found is None:
        return CurrentUser(auth_mode=settings.AUTH_MODE, token_rejected=True)
    user = CurrentUser(
        is_authenticated=True,
        username=f"token:{found['name']}",
        role=found["role"],
        auth_mode=settings.AUTH_MODE,
        token=found["name"],
        tenant=found.get("tenant") or "",
        signed_in_with="token",
    )
    if found["role"] == token_service.STATUS_ROLE:
        return user
    # A key is its identity (#719): the identity's roles and scopes, read now.
    owner = await identity_service.lookup(found.get("identity") or "")
    if owner is None:
        return replace(user, role="", no_identity=True)
    return replace(
        user,
        role=owner.roles[0] if owner.roles else "",
        roles=owner.roles,
        scopes=owner.scopes,
        identity=owner.unique_name,
        common_name=owner.common_name,
    )


def from_trusted_proxy(request: Request, settings: Settings) -> bool:
    """Whether this request's identity headers can be trusted (#187)."""
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    trusted = any(address in network for network in settings.AUTH_TRUSTED_PROXIES)
    if trusted and settings.AUTH_PROXY_SECRET:
        sent = request.headers.get(PROXY_SECRET_HEADER, "")
        trusted = hmac.compare_digest(sent.encode(), settings.AUTH_PROXY_SECRET.encode())
    if not trusted and str(address) not in _logged_untrusted and len(_logged_untrusted) < 100:
        _logged_untrusted.add(str(address))
        logger.warning(
            "Header auth: ignoring identity headers from {} (not in AUTH_TRUSTED_PROXIES{})",
            address,
            " or wrong/missing proxy secret" if settings.AUTH_PROXY_SECRET else "",
        )
    return trusted


async def resolve_user(request: Request, settings: Settings) -> CurrentUser:
    """Who sent *request*: an API token; else the sign-in proxy's headers; else the
    app's own sign-in (AD, #717), from ``Authorization: Bearer`` or its cookie."""
    token = bearer_token(request)
    if token:
        return await _token_user(token, settings)
    anonymous = CurrentUser(auth_mode=settings.AUTH_MODE)
    if settings.AUTH_MODE != "header":
        return anonymous
    if request.headers.get(settings.AUTH_HEADER_USER, "").strip() and from_trusted_proxy(
        request, settings
    ):
        return await _proxy_user(request, settings)
    return await _app_user(request, settings) or anonymous


async def _proxy_user(request: Request, settings: Settings) -> CurrentUser:
    username = request.headers.get(settings.AUTH_HEADER_USER, "").strip()
    groups_header = request.headers.get(settings.AUTH_HEADER_GROUPS, "")
    groups = [item.strip() for item in re.split(r"[;,]", groups_header) if item.strip()]
    signed_in = CurrentUser(
        is_authenticated=bool(username),
        username=username,
        groups=groups,
        auth_mode=settings.AUTH_MODE,
        signed_in_with="proxy",
    )
    if not username:
        return signed_in
    return await with_identity(signed_in, settings)


def not_signed_in(settings: Settings) -> Refusal:
    """401, and with AD sign-in on, where to sign in (#717)."""
    body: dict = {"error": "Authentication required"}
    if app_sign_in_on(settings):
        body["sign_in"] = SIGN_IN_PATH
    return body, 401


def app_token(request: Request) -> str:
    """The app's own sign-in token (#717): ``Authorization: Bearer`` with anything but
    an API token of ours, else the sign-in cookie; "" without either."""
    scheme, _, value = request.headers.get("authorization", "").strip().partition(" ")
    value = value.strip()
    if scheme.lower() == "bearer" and value and not token_service.is_token(value):
        return value
    return request.cookies.get(login_service.COOKIE, "")


def app_sign_in_on(settings: Settings) -> bool:
    """AD sign-in (#717): with AD_HOST, in header mode (the proxy optional)."""
    return settings.AUTH_MODE == "header" and ad_service.enabled(settings)


async def _app_user(request: Request, settings: Settings) -> CurrentUser | None:
    """The identity a valid sign-in token names (its roles read now, not from the
    token); None without a valid one. No identity any more: no access."""
    if not app_sign_in_on(settings):
        return None
    claims = login_service.verify(app_token(request), settings)
    if claims is None:
        return None
    user = CurrentUser(
        is_authenticated=True,
        username=claims["sub"],
        auth_mode=settings.AUTH_MODE,
        signed_in_with="app",
    )
    found = await identity_service.lookup(user.username)
    if found is None:
        return replace(user, no_identity=True)
    return replace(
        user,
        role=found.roles[0] if found.roles else "",
        roles=found.roles,
        scopes=found.scopes,
        identity=found.unique_name,
        common_name=found.common_name,
    )


async def with_identity(user: CurrentUser, settings: Settings) -> CurrentUser:
    """*user* (signed in by name) with their identity's roles and scopes (#715). With
    none: the role their groups give (AUTH_GROUP_ROLES, as before), or no access."""
    found = await identity_service.lookup(user.username)
    if found is not None:
        return replace(
            user,
            role=found.roles[0] if found.roles else "",
            roles=found.roles,
            scopes=found.scopes,
            identity=found.unique_name,
            common_name=found.common_name,
        )
    if settings.AUTH_GROUP_ROLES:
        role = role_from_groups(user.groups, settings)
        identity_service.note_unknown(user.username, f"role from groups: {role or 'none'}")
        return replace(user, role=role, roles=(role,) if role else ())
    identity_service.note_unknown(user.username, "no access: AUTH_GROUP_ROLES=false")
    return replace(user, no_identity=True)


def disabled_mode_allows(method: str, open_when_disabled: bool, settings: Settings) -> bool:
    """Whether a protected route runs with AUTH_MODE=disabled (#188): reads and the
    board's own changes always; administrative changes only with
    ALLOW_UNAUTHENTICATED_WRITES."""
    return method in SAFE_METHODS or open_when_disabled or settings.ALLOW_UNAUTHENTICATED_WRITES


def granted_scopes(user: CurrentUser, settings: Settings) -> frozenset[str]:
    """The scopes this caller holds (#685, #711), wildcards kept: a signed-in user's
    or token's role's. Nobody signed in (or no role) in header mode without
    AUTH_REQUIRE_VIEWER: the viewer's, which every route without a role check gave
    anyone before (#592)."""
    if user.is_authenticated and (user.roles or user.scopes):
        held: set[str] = set(user.scopes)
        for role in user.roles:
            held |= permissions.role_scopes(role)
        return frozenset(held)
    if user.is_authenticated and user.role in permissions.ROLES:
        return permissions.role_scopes(user.role)
    if user.token or is_status_token(user):
        return frozenset()
    if settings.AUTH_MODE == "header" and not settings.AUTH_REQUIRE_VIEWER:
        return permissions.role_scopes("viewer")
    return frozenset()


def granted(user: CurrentUser, settings: Settings) -> frozenset[Permission]:
    """The permissions ``granted_scopes`` covers."""
    return permissions.covered(granted_scopes(user, settings))


def effective_permissions(user: CurrentUser, settings: Settings) -> frozenset[Permission]:
    """What this caller may do, as ``permission_problem`` answers it route by route
    (#693): the pages show exactly what the API allows. With AUTH_MODE=disabled (no
    token): every read and the board's own changes, everything with
    ALLOW_UNAUTHENTICATED_WRITES; otherwise ``granted``."""
    if settings.AUTH_MODE == "disabled" and not user.token:
        if settings.ALLOW_UNAUTHENTICATED_WRITES:
            return permissions.ALL
        return permissions.READS | permissions.OPEN_WHEN_DISABLED
    if settings.AUTH_MODE not in ("disabled", "header"):
        return frozenset()
    return granted(user, settings)


def access(user: CurrentUser, settings: Settings) -> dict:
    """For the pages (#693): the role (null when none), its label, the page it opens
    on, the menu's pages it may open and its effective permissions."""
    have = effective_permissions(user, settings)
    role = user.role if user.is_authenticated and user.role in permissions.ROLES else None
    definition = permissions.ROLES.get(role or "")
    if definition:
        label = definition.label
    elif user.is_authenticated:
        label = "No identity" if user.no_identity else "No role"
    else:
        label = "Not signed in"
    return {
        "role": role,
        "roles": [name for name in user.roles if name in permissions.ROLES],
        "identity": user.identity or None,
        "label": label,
        "landing": definition.landing if definition else "/",
        "pages": [key for key, need in permissions.PAGE_PERMISSIONS.items() if need in have],
        "permissions": sorted(str(p) for p in have),
    }


def permission_problem(
    user: CurrentUser,
    settings: Settings,
    required: tuple[str, ...],
    method: str,
    open_when_disabled: bool = False,
    params: dict | None = None,
) -> Refusal | None:
    """None when this request may use a route needing one of *required* (scopes, a
    ``{name}`` filled in from the path *params*, #711), else the refusal: 401 nobody
    signed in, 403 a role without any of them (naming the first, and the lowest of
    the old roles that has it)."""
    # A token is checked like a signed-in user, also with AUTH_MODE=disabled.
    if settings.AUTH_MODE == "disabled" and not user.token:
        if disabled_mode_allows(method, open_when_disabled, settings):
            return None
        return {
            "error": "Authentication required for this change",
            "detail": "Set AUTH_MODE=header behind an authenticating proxy, "
            "or ALLOW_UNAUTHENTICATED_WRITES=true to allow it without authentication.",
        }, 403
    if settings.AUTH_MODE not in ("disabled", "header"):
        # Startup refuses another value; one set in code answers as the current app.
        return {"error": "Unsupported AUTH_MODE configuration"}, 503
    held = granted_scopes(user, settings)
    wanted = [scope_rules.fill(str(scope), params or {}) for scope in required]
    if any(scope_rules.is_scope_covered(scope, held) for scope in wanted):
        return None
    if not user.is_authenticated:
        return not_signed_in(settings)
    if user.no_identity:
        return {
            "error": NO_ACCESS,
            "detail": f"{user.username!r} has no identity here: ask an admin to add you.",
            "current_role": None,
        }, 403
    return {
        "error": "Insufficient permissions",
        "required_permission": wanted[0],
        "required_role": permissions.lowest_old_role(wanted[0]),
        "current_role": user.role or None,
    }, 403


def may_administer(user: CurrentUser, settings: Settings) -> bool:
    """What a POST needing ``admin`` is allowed (#530), so pages show the
    administration only to those who may use it."""
    return Permission.ADMIN in effective_permissions(user, settings)


def may_change_the_board(user: CurrentUser, settings: Settings) -> bool:
    """What acknowledging is allowed (the board's own changes, open with
    AUTH_MODE=disabled): the pages' ``can_write`` (deprecated, #693)."""
    return Permission.ALERTS_ACK in effective_permissions(user, settings)


def is_status_token(user: CurrentUser) -> bool:
    return bool(user.token) and user.role == token_service.STATUS_ROLE


def status_access(user: CurrentUser, settings: Settings, tenant: str) -> Refusal | None:
    """None when this request may read *tenant*'s status: a status token only its
    own tenant; anyone else as the board (the viewer role with AUTH_REQUIRE_VIEWER
    in header mode)."""
    if user.token:
        if is_status_token(user):
            if user.tenant.casefold() == tenant.strip().casefold():
                return None
            return {"error": "This token is for another tenant"}, 403
        if scope_rules.is_scope_covered(Permission.SESSION_BASIC, granted_scopes(user, settings)):
            return None
        return {"error": "Insufficient permissions", "required_role": "viewer"}, 403
    return _viewer_problem(user, settings)


def _viewer_problem(user: CurrentUser, settings: Settings) -> Refusal | None:
    if settings.AUTH_MODE != "header" or not settings.AUTH_REQUIRE_VIEWER:
        return None
    if not user.is_authenticated:
        return not_signed_in(settings)
    if user.no_identity:
        return {
            "error": NO_ACCESS,
            "detail": f"{user.username!r} has no identity here: ask an admin to add you.",
        }, 403
    if not scope_rules.is_scope_covered(Permission.SESSION_BASIC, granted_scopes(user, settings)):
        return {"error": "Insufficient permissions", "required_role": "viewer"}, 403
    return None


def is_cross_site_write(request: Request, settings: Settings) -> bool:
    """A change sent by a browser from another site (CSRF), except from CORS_ORIGINS.

    Behind a sign-in proxy the session cookie comes along, so a page elsewhere
    could act as the signed-in user. Browsers say where a request comes from:
    Sec-Fetch-Site, else Origin. Scripts and webhooks send neither (or an API
    token, which a page can't add) and are not affected.
    """
    # A token in Authorization is one a page elsewhere can't add (the cookie it can).
    if request.method in SAFE_METHODS or bearer_token(request):
        return False
    if request.headers.get("authorization", "").strip().lower().startswith("bearer "):
        return False
    # MCP has its own, stricter rule: no Origin outside MCP_ALLOWED_ORIGINS.
    if request.url.path == MCP_PATH:
        return False
    origin = request.headers.get("origin", "").strip()
    if origin and origin in settings.CORS_ORIGINS:
        return False
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site:
        return fetch_site not in {"same-origin", "none"}
    if not origin:
        return False
    # The host alone, as the current app: behind the sign-in proxy this app sees
    # http:// where the browser used https://, and a proxy may pass the Host
    # without the browser's port. Another port on the same host is caught by
    # Sec-Fetch-Site (same-site), which every current browser sends.
    host = urlsplit("//" + request.headers.get("host", "")).hostname
    return origin == "null" or urlsplit(origin).hostname != host


def check_request(request: Request, user: CurrentUser, settings: Settings) -> Refusal | None:
    """Before every route: a change from another site is refused in every mode; a
    rejected API token is a 401 everywhere but the public paths; a status token
    opens only the status page; with AUTH_REQUIRE_VIEWER in header mode, everything
    else needs the viewer role (#188)."""
    path = request.url.path
    if is_cross_site_write(request, settings):
        logger.warning(
            "Refused a cross-site {} {} (Origin {!r})",
            request.method,
            path,
            request.headers.get("origin"),
        )
        return {"error": "Changes from another site are refused"}, 403
    if path in PUBLIC_PATHS:
        return None
    # The sign-in page and its routes, for someone not signed in yet (#717).
    if path in SIGN_IN_PATHS and app_sign_in_on(settings):
        return None
    # A script should learn its token stopped working, also where anyone may read.
    if user.token_rejected:
        return {"error": "Invalid, expired or revoked API token"}, 401
    status_path = path.startswith((STATUS_PAGE_PREFIX, STATUS_API_PREFIX, FRONTEND_ASSETS_PREFIX))
    if is_status_token(user) and not status_path:
        return {"error": "This token only opens its tenant's status page"}, 403
    if status_path:
        return None
    return _viewer_problem(user, settings)
