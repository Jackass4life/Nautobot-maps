"""Who the API sees: lets web/ show the user and hide what they may not do."""

from fastapi import APIRouter

from app.auth.dependencies import CurrentUserDep
from app.core.settings import get_settings
from app.services import auth_service
from app.v1 import sections
from app.v1.auth.dtos import MeDto

router = APIRouter(prefix="/me", tags=sections.tag("auth"))


@router.get("", operation_id="getCurrentUser", response_model=MeDto)
def get_current_user(user: CurrentUserDep) -> MeDto:
    settings = get_settings()
    return MeDto(
        username=user.username or None,
        # Not signed in, no role (role_from_groups gives the default role to anyone).
        role=(user.role or None) if user.is_authenticated else None,
        groups=user.groups,
        auth_mode=settings.auth_mode,
        # Writes as the board's own changes are (open with AUTH_MODE=disabled).
        can_write=auth_service.role_problem(user, settings, "operator", "POST", True) is None,
        can_administer=auth_service.may_administer(user, settings),
    )
