"""What every area's answers share (#449)."""

from pydantic import BaseModel, ConfigDict


class Answer(BaseModel):
    """A JSON object the API sends.  A field the model doesn't name fails the
    tests' check, so a new field gets documented when it is added."""

    model_config = ConfigDict(extra="forbid")


# The one error model of the whole API (#604): defined beside the error handlers.
from app.utils.errors import Error  # noqa: E402, F401
