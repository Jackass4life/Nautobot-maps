from typing import Literal

from pydantic import BaseModel, Field

from app.v1.common.dtos import Access


class MeDto(BaseModel):
    """Who the API sees: `username` is null when not signed in, `role` null without one."""

    username: str | None
    role: str | None
    groups: list[str]
    auth_mode: Literal["disabled", "header"]
    access: Access
    # From access.permissions (#693): alerts.ack and admin.
    can_write: bool = Field(
        deprecated=True, description="Deprecated: alerts.ack in access.permissions."
    )
    can_administer: bool = Field(
        deprecated=True, description="Deprecated: admin in access.permissions."
    )
