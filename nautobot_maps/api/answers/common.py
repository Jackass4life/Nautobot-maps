"""What every area's answers share (#449)."""

from pydantic import BaseModel, ConfigDict, Field


class Answer(BaseModel):
    """A JSON object the API sends.  A field the model doesn't name fails the
    tests' check, so a new field gets documented when it is added."""

    model_config = ConfigDict(extra="forbid")


class Error(Answer):
    """Every refusal and error: one plain sentence."""

    error: str = Field(examples=["Not found"])
