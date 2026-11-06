"""FastAPI dependencies for authentication and roles (#582)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from app.services import auth_service
from app.services.auth_service import CurrentUser
from app.utils.config import Role, get_settings
from app.utils.errors import ForbiddenError, UnauthorizedError


def get_current_user(request: Request) -> CurrentUser:
    user = getattr(request.state, "user", None)
    if user is None:
        user = auth_service.resolve_user(request, get_settings())
        request.state.user = user
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def refuse_cross_site_writes(request: Request) -> None:
    if auth_service.is_cross_site_write(request, get_settings()):
        raise ForbiddenError("Changes from another site are refused")


def require_role(role: Role) -> Callable[[CurrentUser], None]:
    """Dependency: the caller needs at least `role` (401 if not signed in, else 403)."""

    def check(user: CurrentUserDep) -> None:
        if user.has_role(role):
            return
        if user.username is None and get_settings().auth_mode == "header":
            raise UnauthorizedError("Sign in required")
        raise ForbiddenError(f"Needs the {role} role")

    return check


def require_reader(user: CurrentUserDep) -> None:
    """Reads are public unless AUTH_REQUIRE_VIEWER."""
    if get_settings().auth_require_viewer:
        require_role("viewer")(user)
