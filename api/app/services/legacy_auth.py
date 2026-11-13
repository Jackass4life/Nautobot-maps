"""The current app's ``auth`` functions (nautobot_maps/auth.py) for the code ported from
it (#604): its routes, MCP, the page config. One sign-in, not two: each call is
app/services/auth_service.py's rules (#592) on the request being answered
(``requestctx``), with the user resolved once per request (shared with
``app.auth.dependencies.get_current_user``).
"""

from app.context import request as requestctx
from app.core.settings import get_settings
from app.db import session
from app.services import auth_service
from app.services.auth_service import ROLE_LEVELS, CurrentUser

SUPPORTED_MODES = {"disabled", "header"}
PUBLIC_PATHS = auth_service.PUBLIC_PATHS
STATUS_PAGE_PREFIX = auth_service.STATUS_PAGE_PREFIX
STATUS_API_PREFIX = auth_service.STATUS_API_PREFIX


def normalize_role(role: str) -> str:
    role = (role or "").strip().lower()
    return role if role in ROLE_LEVELS else ""


def role_level(role: str) -> int:
    return ROLE_LEVELS.get(normalize_role(role), 0)


def is_config_valid() -> bool:
    return get_settings().auth_mode in SUPPORTED_MODES


def _user() -> CurrentUser:
    """The request's user, resolved by every router's sign-in check before the route
    runs; else (a route's thread) on the app's event loop (#643)."""
    request = requestctx.current().starlette
    user = getattr(request.state, "user", None)
    if user is None:
        user = session.from_sync(auth_service.resolve_user, request, get_settings())
        request.state.user = user
    return user


def get_current_user() -> dict:
    """The current user as the current app's dict (``token`` and ``tenant`` with a token)."""
    user = _user()
    current = {
        "is_authenticated": user.is_authenticated,
        "username": user.username,
        "groups": list(user.groups),
        "role": user.role,
        "auth_mode": user.auth_mode,
    }
    if user.token or user.token_rejected:
        current |= {
            "token": user.token,
            "tenant": user.tenant,
            "token_rejected": user.token_rejected,
        }
    return current


def bearer_token() -> str:
    return auth_service.bearer_token(requestctx.current().starlette)


def may_administer() -> bool:
    return auth_service.may_administer(_user(), get_settings())


def is_status_token(user: dict) -> bool:
    return bool(user.get("token")) and user.get("role") == "status"


def status_access(tenant: str):  # type: ignore[no-untyped-def]
    return auth_service.status_access(_user(), get_settings(), tenant)


def check_viewer():  # type: ignore[no-untyped-def]
    view = requestctx.current()
    return auth_service.check_request(view.starlette, _user(), get_settings())
