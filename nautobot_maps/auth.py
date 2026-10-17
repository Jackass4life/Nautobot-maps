"""Optional header-based authentication and roles (#165).

A trusted reverse proxy sets the user and group headers (AUTH_MODE=header);
``@auth.require_role("operator")`` protects write endpoints.  An API token
(``Authorization: Bearer nmt_...``, #297) signs in as ``token:<name>`` with
the token's role, in either mode and from any address.
"""

import hmac
import ipaddress
import logging
import re
from functools import wraps

from flask import g, jsonify, request

from nautobot_maps import settings, tokens

logger = logging.getLogger(__name__)

ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}
SUPPORTED_MODES = {"disabled", "header"}


def normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    return role if role in ROLE_LEVELS else ""


def is_config_valid() -> bool:
    return settings.AUTH_MODE in SUPPORTED_MODES


def flask_run_host() -> str:
    return "127.0.0.1" if settings.AUTH_MODE == "header" else "0.0.0.0"


PROXY_SECRET_HEADER = "X-Auth-Proxy-Secret"
# Addresses already warned about, so a scanner can't flood the log.
_logged_untrusted: set[str] = set()


def request_from_trusted_proxy() -> bool:
    """Whether this request's identity headers can be trusted (#187).

    The direct peer must be in AUTH_TRUSTED_PROXIES and, when AUTH_PROXY_SECRET
    is set, send it.  Anyone else could simply set X-Forwarded-User themselves.
    """
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    trusted = any(address in network for network in settings.AUTH_TRUSTED_PROXIES)
    if trusted and settings.AUTH_PROXY_SECRET:
        trusted = hmac.compare_digest(request.headers.get(PROXY_SECRET_HEADER, ""), settings.AUTH_PROXY_SECRET)
    if not trusted and str(address) not in _logged_untrusted and len(_logged_untrusted) < 100:
        _logged_untrusted.add(str(address))
        logger.warning(
            "Header auth: ignoring identity headers from %s (not in AUTH_TRUSTED_PROXIES%s)",
            address,
            " or wrong/missing proxy secret" if settings.AUTH_PROXY_SECRET else "",
        )
    return trusted


def role_level(role: str) -> int:
    return ROLE_LEVELS.get(normalize_role(role), 0)


def role_from_groups(groups: list[str]) -> str:
    normalized_groups = {group.strip().lower() for group in groups if group.strip()}
    if normalized_groups & settings.AUTH_ADMIN_GROUPS:
        return "admin"
    if normalized_groups & settings.AUTH_OPERATOR_GROUPS:
        return "operator"
    if normalized_groups & settings.AUTH_VIEWER_GROUPS:
        return "viewer"
    return settings.AUTH_DEFAULT_ROLE


def bearer_token() -> str:
    """The API token in ``Authorization: Bearer``, or "" (also for a Bearer
    value that isn't ours, e.g. a proxy's own access token)."""
    scheme, _, value = request.headers.get("Authorization", "").strip().partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and tokens.is_token(value) else ""


def _token_user(token: str) -> dict:
    found = None
    try:
        found = tokens.verify(token)
    except Exception as exc:  # the database; never log the token
        logger.warning("API token check failed: %s", type(exc).__name__)
    return {
        "is_authenticated": found is not None,
        "username": f"token:{found['name']}" if found else "",
        "groups": [],
        "role": found["role"] if found else "",
        "auth_mode": settings.AUTH_MODE,
        "token": found["name"] if found else "",
        # A status token's tenant (#291); "" for other tokens.
        "tenant": (found.get("tenant") or "") if found else "",
        "token_rejected": found is None,
    }


def get_current_user() -> dict:
    """Return the current authenticated user context for the request."""
    current = getattr(g, "_current_user", None)
    if current is not None:
        return current

    token = bearer_token()
    if token:
        g._current_user = _token_user(token)
        return g._current_user

    current = {
        "is_authenticated": False,
        "username": "",
        "groups": [],
        "role": "",
        "auth_mode": settings.AUTH_MODE,
    }
    if settings.AUTH_MODE == "disabled":
        g._current_user = current
        return current

    if settings.AUTH_MODE == "header":
        if not request_from_trusted_proxy():
            g._current_user = current
            return current
        username = request.headers.get(settings.AUTH_HEADER_USER, "").strip()
        groups_header = request.headers.get(settings.AUTH_HEADER_GROUPS, "")
        groups = [item.strip() for item in re.split(r"[;,]", groups_header) if item.strip()]
        current = {
            "is_authenticated": bool(username),
            "username": username,
            "groups": groups,
            "role": role_from_groups(groups),
            "auth_mode": settings.AUTH_MODE,
        }

    g._current_user = current
    return current


def disabled_mode_allows(method: str, open_when_disabled: bool) -> bool:
    """Whether a protected route runs with AUTH_MODE=disabled (#188).

    Reads and the routes the board itself uses (*open_when_disabled*, e.g.
    adding a case) always do; administrative writes only with
    ALLOW_UNAUTHENTICATED_WRITES.
    """
    return method in ("GET", "HEAD", "OPTIONS") or open_when_disabled or settings.ALLOW_UNAUTHENTICATED_WRITES


def require_role(required_role: str, open_when_disabled: bool = False):
    """Require *required_role*; see ``disabled_mode_allows`` for AUTH_MODE=disabled."""
    normalized_required_role = normalize_role(required_role)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # A token is checked like a signed-in user, also with AUTH_MODE=disabled.
            if settings.AUTH_MODE == "disabled" and not get_current_user().get("token"):
                if disabled_mode_allows(request.method, open_when_disabled):
                    return func(*args, **kwargs)
                return jsonify(
                    {
                        "error": "Authentication required for this change",
                        "detail": "Set AUTH_MODE=header behind an authenticating proxy, "
                        "or ALLOW_UNAUTHENTICATED_WRITES=true to allow it without authentication.",
                    }
                ), 403
            if not is_config_valid():
                return jsonify({"error": "Unsupported AUTH_MODE configuration"}), 503

            current_user = get_current_user()
            if not current_user["is_authenticated"]:
                return jsonify({"error": "Authentication required"}), 401
            if role_level(current_user["role"]) < role_level(normalized_required_role):
                return jsonify(
                    {
                        "error": "Insufficient permissions",
                        "required_role": normalized_required_role,
                        "current_role": current_user["role"] or None,
                    }
                ), 403
            return func(*args, **kwargs)

        # Shown by the API explorer (#230).
        wrapper.required_role = normalized_required_role
        return wrapper

    return decorator


# Reachable without the viewer role even with AUTH_REQUIRE_VIEWER: probes and
# Prometheus scrapes send no identity headers.  /metrics has only counts,
# no site or device names (#200).
# error.css: the error page's styles, no longer inline (#370); any error
# page may need them, signed in or not, and they carry no data.
PUBLIC_PATHS = {"/healthz", "/metrics", "/static/css/error.css"}
# A tenant's status page (#291): the page and its assets carry no data, so a
# customer's browser loads them without SSO; /api/status/ checks access itself
# (``status_access``).
STATUS_PAGE_PREFIX = "/status/"
STATUS_API_PREFIX = "/api/status/"
# Every module the page loads, imports included (#294); a test checks the list.
STATUS_PAGE_ASSETS = {
    "/static/css/alerts.css",
    "/static/js/status/main.js",
    "/static/js/status/page.js",
    "/static/js/lib/format.js",
    "/static/js/lib/html.js",
}


def is_status_token(user: dict) -> bool:
    return bool(user.get("token")) and user.get("role") == tokens.STATUS_ROLE


def status_access(tenant: str):
    """None when this request may read *tenant*'s status, else a response.

    A status token: only its own tenant.  Anyone else: as the board, i.e.
    the viewer role when AUTH_REQUIRE_VIEWER is on in header mode.
    """
    current_user = get_current_user()
    if current_user.get("token"):
        if is_status_token(current_user):
            if current_user.get("tenant", "").casefold() == tenant.strip().casefold():
                return None
            return jsonify({"error": "This token is for another tenant"}), 403
        if role_level(current_user["role"]) >= role_level("viewer"):
            return None
        return jsonify({"error": "Insufficient permissions", "required_role": "viewer"}), 403
    if settings.AUTH_MODE != "header" or not settings.AUTH_REQUIRE_VIEWER:
        return None
    if not current_user["is_authenticated"]:
        return jsonify({"error": "Authentication required"}), 401
    if role_level(current_user["role"]) < role_level("viewer"):
        return jsonify({"error": "Insufficient permissions", "required_role": "viewer"}), 403
    return None


def check_viewer():
    """``before_request`` hook: with AUTH_REQUIRE_VIEWER in header mode, every
    page and API needs at least the viewer role (#188), and a rejected API
    token is a 401 (#297).  Returns a response to stop the request, or None."""
    if request.path in PUBLIC_PATHS:
        return None
    # A wrong, expired or revoked token is refused on every path but the
    # public ones above, also where anyone may read: a script should learn
    # its token stopped working.
    if bearer_token() and get_current_user().get("token_rejected"):
        return jsonify({"error": "Invalid, expired or revoked API token"}), 401
    status_path = request.path.startswith((STATUS_PAGE_PREFIX, STATUS_API_PREFIX)) or request.path in STATUS_PAGE_ASSETS
    if bearer_token() and is_status_token(get_current_user()) and not status_path:
        return jsonify({"error": "This token only opens its tenant's status page"}), 403
    if status_path:
        return None
    if settings.AUTH_MODE != "header" or not settings.AUTH_REQUIRE_VIEWER:
        return None
    current_user = get_current_user()
    if not current_user["is_authenticated"]:
        return jsonify({"error": "Authentication required"}), 401
    if role_level(current_user["role"]) < role_level("viewer"):
        return jsonify({"error": "Insufficient permissions", "required_role": "viewer"}), 403
    return None
