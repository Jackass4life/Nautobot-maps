"""FastAPI dependencies for sign-in and permissions (#582, #592, #685): the rules are
in app/services/auth_service.py, the matrix in app/auth/permissions.py."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from app.auth import permissions
from app.auth.permissions import Permission
from app.core.exceptions import ApiError
from app.core.settings import get_settings
from app.services import auth_service
from app.services.auth_service import CurrentUser


async def get_current_user(request: Request) -> CurrentUser:
    """Once per request: a token is verified (and its last use written) once."""
    user = getattr(request.state, "user", None)
    if user is None:
        user = await auth_service.resolve_user(request, get_settings())
        request.state.user = user
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def check_request(request: Request, user: CurrentUserDep) -> None:
    """Every router's dependency: cross-site changes, rejected tokens, status
    tokens, AUTH_REQUIRE_VIEWER (``auth_service.check_request``)."""
    refused = auth_service.check_request(request, user, get_settings())
    if refused is not None:
        raise ApiError(*refused)


def require_permission(
    *required: Permission, open_when_disabled: bool = False
) -> Callable[..., None]:
    """Dependency: the caller's role has one of *required* (app/auth/permissions.py,
    #685). With AUTH_MODE=disabled, reads and *open_when_disabled* routes (the board's
    own changes) run for anyone; the rest need ALLOW_UNAUTHENTICATED_WRITES."""

    def check(request: Request, user: CurrentUserDep) -> None:
        refused = auth_service.permission_problem(
            user, get_settings(), required, request.method, open_when_disabled
        )
        if refused is not None:
            raise ApiError(*refused)

    check.required_permissions = tuple(str(p) for p in required)  # type: ignore[attr-defined]
    check.open_when_disabled = open_when_disabled  # type: ignore[attr-defined]
    return check


def public() -> None:
    """Dependency: a route anyone reaches without signing in (#685: every route names
    what it needs; this one needs nothing): health, metrics, the status page."""


public.required_permissions = (permissions.PUBLIC,)  # type: ignore[attr-defined]
