"""Optional header-based authentication and roles (#165).

A trusted reverse proxy sets the user and group headers (AUTH_MODE=header);
``@auth.require_role("operator")`` protects write endpoints.
"""

import hmac
import ipaddress
import logging
import re
from functools import wraps

from flask import g, jsonify, request

from nautobot_maps import settings

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


def get_current_user() -> dict:
    """Return the current authenticated user context for the request."""
    current = getattr(g, "_current_user", None)
    if current is not None:
        return current

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
            if settings.AUTH_MODE == "disabled":
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
PUBLIC_PATHS = {"/healthz", "/metrics"}


def check_viewer():
    """``before_request`` hook: with AUTH_REQUIRE_VIEWER in header mode, every
    page and API needs at least the viewer role (#188).  Returns a response
    to stop the request, or None."""
    if settings.AUTH_MODE != "header" or not settings.AUTH_REQUIRE_VIEWER or request.path in PUBLIC_PATHS:
        return None
    current_user = get_current_user()
    if not current_user["is_authenticated"]:
        return jsonify({"error": "Authentication required"}), 401
    if role_level(current_user["role"]) < role_level("viewer"):
        return jsonify({"error": "Insufficient permissions", "required_role": "viewer"}), 403
    return None
