"""What every area's answers share (#449)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Answer(BaseModel):
    """A JSON object the API sends.  A field the model doesn't name fails the
    tests' check, so a new field gets documented when it is added."""

    model_config = ConfigDict(extra="forbid")


class Error(Answer):
    """Every refusal and error: one plain sentence, and for a role refusal the
    role it needs."""

    error: str = Field(examples=["Insufficient permissions"])
    detail: str | None = Field(None, description="How to allow it, when the setup refuses it.")
    required_role: Literal["viewer", "operator", "admin"] | None = None
    current_role: str | None = None
    missing_device_ids: list[str] | None = Field(None, description="Devices with no open alert: nothing was changed.")
