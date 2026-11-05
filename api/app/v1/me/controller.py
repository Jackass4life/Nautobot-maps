"""Who the API sees: lets web/ show the user and hide what they may not do."""

from fastapi import APIRouter

from app.services import auth_service
from app.utils.auth import CurrentUserDep
from app.utils.config import get_settings
from app.v1.me.dtos import MeDto

router = APIRouter(prefix="/me", tags=["auth"])


@router.get("", operation_id="getCurrentUser", response_model=MeDto)
def get_current_user(user: CurrentUserDep) -> MeDto:
    return auth_service.to_me_dto(user, get_settings())
