from typing import Literal

from pydantic import BaseModel

from app.utils.config import Role


class MeDto(BaseModel):
    """Who the API sees: `username` is null when not signed in, `role` null without one."""

    username: str | None
    role: Role | None
    groups: list[str]
    auth_mode: Literal["disabled", "header"]
