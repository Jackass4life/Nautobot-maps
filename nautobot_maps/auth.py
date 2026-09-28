"""Optional header-based authentication and roles (#165).

A trusted reverse proxy sets the user and group headers (AUTH_MODE=header);
``@auth.require_role("operator")`` protects write endpoints.
"""

import re
from functools import wraps

from flask import g, jsonify, request

from nautobot_maps import settings

ROLE_LEVELS = {"viewer": 1, "operator": 2, "admin": 3}
SUPPORTED_MODES = {"disabled", "header"}


def normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    return role if role in ROLE_LEVELS else ""


def is_config_valid() -> bool:
    return settings.AUTH_MODE in SUPPORTED_MODES


def flask_run_host() -> str:
    return "127.0.0.1" if settings.AUTH_MODE == "header" else "0.0.0.0"


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


def require_role(required_role: str):
    """Allow access when auth is disabled or the current user meets *required_role*."""
    normalized_required_role = normalize_role(required_role)

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if settings.AUTH_MODE == "disabled":
                return func(*args, **kwargs)
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

        return wrapper

    return decorator
