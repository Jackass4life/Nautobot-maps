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
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import Request

from app import session
from app.services import token_service
from app.utils.config import Settings

logger = logging.getLogger(__name__)

ROLE_LEVELS: dict[str, int] = {"viewer": 1, "operator": 2, "admin": 3}
PROXY_SECRET_HEADER = "X-Auth-Proxy-Secret"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Reachable without the viewer role even with AUTH_REQUIRE_VIEWER: probes and
# Prometheus scrapes send no identity headers; /metrics has only counts (#200).
PUBLIC_PATHS = {"/healthz", "/metrics", "/api/healthz"}
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


def role_level(role: str) -> int:
    return ROLE_LEVELS.get((role or "").strip().lower(), 0)


def role_from_groups(groups: list[str], settings: Settings) -> str:
    names = {group.strip().lower() for group in groups if group.strip()}
    if names & settings.auth_admin_groups:
        return "admin"
    if names & settings.auth_operator_groups:
        return "operator"
    if names & settings.auth_viewer_groups:
        return "viewer"
    return settings.auth_default_role


def bearer_token(request: Request) -> str:
    """The API token in ``Authorization: Bearer``, or "" (also for a Bearer value
    that isn't ours, e.g. a proxy's own access token)."""
    scheme, _, value = request.headers.get("authorization", "").strip().partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and token_service.is_token(value) else ""


def _verify_token(token: str) -> dict | None:
    """The token's owner, on a session of its own: last_used_at is committed
    whatever the request does."""
    with session.SessionLocal() as own:
        return token_service.verify(own, token)


def _token_user(token: str, settings: Settings) -> CurrentUser:
    found = None
    try:
        found = _verify_token(token)
    except Exception as exc:  # noqa: BLE001 - the database; never log the token
        logger.warning("API token check failed: %s", type(exc).__name__)
    if found is None:
        return CurrentUser(auth_mode=settings.auth_mode, token_rejected=True)
    return CurrentUser(
        is_authenticated=True,
        username=f"token:{found['name']}",
        role=found["role"],
        auth_mode=settings.auth_mode,
        token=found["name"],
        tenant=found.get("tenant") or "",
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
    trusted = any(address in network for network in settings.auth_trusted_proxies)
    if trusted and settings.auth_proxy_secret:
        sent = request.headers.get(PROXY_SECRET_HEADER, "")
        trusted = hmac.compare_digest(sent.encode(), settings.auth_proxy_secret.encode())
    if not trusted and str(address) not in _logged_untrusted and len(_logged_untrusted) < 100:
        _logged_untrusted.add(str(address))
        logger.warning(
            "Header auth: ignoring identity headers from %s (not in AUTH_TRUSTED_PROXIES%s)",
            address,
            " or wrong/missing proxy secret" if settings.auth_proxy_secret else "",
        )
    return trusted


def resolve_user(request: Request, settings: Settings) -> CurrentUser:
    token = bearer_token(request)
    if token:
        return _token_user(token, settings)
    anonymous = CurrentUser(auth_mode=settings.auth_mode)
    if settings.auth_mode != "header" or not from_trusted_proxy(request, settings):
        return anonymous
    username = request.headers.get(settings.auth_header_user, "").strip()
    groups_header = request.headers.get(settings.auth_header_groups, "")
    groups = [item.strip() for item in re.split(r"[;,]", groups_header) if item.strip()]
    return CurrentUser(
        is_authenticated=bool(username),
        username=username,
        groups=groups,
        role=role_from_groups(groups, settings),
        auth_mode=settings.auth_mode,
    )


def disabled_mode_allows(method: str, open_when_disabled: bool, settings: Settings) -> bool:
    """Whether a protected route runs with AUTH_MODE=disabled (#188): reads and the
    board's own changes always; administrative changes only with
    ALLOW_UNAUTHENTICATED_WRITES."""
    return method in SAFE_METHODS or open_when_disabled or settings.allow_unauthenticated_writes


def role_problem(
    user: CurrentUser,
    settings: Settings,
    required_role: str,
    method: str,
    open_when_disabled: bool = False,
) -> Refusal | None:
    """None when this request may use a route needing *required_role*, else the refusal."""
    # A token is checked like a signed-in user, also with AUTH_MODE=disabled.
    if settings.auth_mode == "disabled" and not user.token:
        if disabled_mode_allows(method, open_when_disabled, settings):
            return None
        return {
            "error": "Authentication required for this change",
            "detail": "Set AUTH_MODE=header behind an authenticating proxy, "
            "or ALLOW_UNAUTHENTICATED_WRITES=true to allow it without authentication.",
        }, 403
    if settings.auth_mode not in ("disabled", "header"):
        # Startup refuses another value; one set in code answers as the current app.
        return {"error": "Unsupported AUTH_MODE configuration"}, 503
    if not user.is_authenticated:
        return {"error": "Authentication required"}, 401
    if role_level(user.role) < role_level(required_role):
        return {
            "error": "Insufficient permissions",
            "required_role": required_role,
            "current_role": user.role or None,
        }, 403
    return None


def may_administer(user: CurrentUser, settings: Settings) -> bool:
    """What ``role_problem("admin")`` allows a POST (#530), so pages show the
    administration only to those who may use it."""
    return role_problem(user, settings, "admin", "POST") is None


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
        if role_level(user.role) >= role_level("viewer"):
            return None
        return {"error": "Insufficient permissions", "required_role": "viewer"}, 403
    return _viewer_problem(user, settings)


def _viewer_problem(user: CurrentUser, settings: Settings) -> Refusal | None:
    if settings.auth_mode != "header" or not settings.auth_require_viewer:
        return None
    if not user.is_authenticated:
        return {"error": "Authentication required"}, 401
    if role_level(user.role) < role_level("viewer"):
        return {"error": "Insufficient permissions", "required_role": "viewer"}, 403
    return None


def is_cross_site_write(request: Request, settings: Settings) -> bool:
    """A change sent by a browser from another site (CSRF), except from CORS_ORIGINS.

    Behind a sign-in proxy the session cookie comes along, so a page elsewhere
    could act as the signed-in user. Browsers say where a request comes from:
    Sec-Fetch-Site, else Origin. Scripts and webhooks send neither (or an API
    token, which a page can't add) and are not affected.
    """
    if request.method in SAFE_METHODS or bearer_token(request):
        return False
    # MCP has its own, stricter rule: no Origin outside MCP_ALLOWED_ORIGINS.
    if request.url.path == MCP_PATH:
        return False
    origin = request.headers.get("origin", "").strip()
    if origin and origin in settings.cors_origins:
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
            "Refused a cross-site %s %s (Origin %r)",
            request.method,
            path,
            request.headers.get("origin"),
        )
        return {"error": "Changes from another site are refused"}, 403
    if path in PUBLIC_PATHS:
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
