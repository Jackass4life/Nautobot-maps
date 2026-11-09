from typing import Literal

from pydantic import BaseModel


class MeDto(BaseModel):
    """Who the API sees: `username` is null when not signed in, `role` null without one."""

    username: str | None
    role: str | None
    groups: list[str]
    auth_mode: Literal["disabled", "header"]
    # By the current app's rules (#592), so pages hide only what the API would refuse.
    can_write: bool
    can_administer: bool
