"""What every section's DTOs share (#449, #624): the answer and query bases, the
one error model, a yes/no query value, and the answer types several sections use.
A section's dtos.py imports only this module, never another section's."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Answer(BaseModel):
    """A JSON object the API sends.  A field the model doesn't name fails the
    tests' check, so a new field gets documented when it is added."""

    model_config = ConfigDict(extra="forbid")


class Query(BaseModel):
    """Query parameters as the Flask routes read them (``routes.common.arg``):
    stripped, and empty means not given.  Unknown ones are ignored."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _stripped(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


def flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes (``routes.common.flag_arg``)."""
    return (value or "").strip().lower() in {"1", "true", "yes"}


# Shared by the map and the alert board.
class Neighbour(Answer):
    """The switch and port an access point is on (#524)."""

    device: str = Field(description='The switch, e.g. "hel0008-acc-sw02".')
    port: str = Field(description='Its port, e.g. "Gi1/0/14"; may be empty.')
    source: str = Field(
        description='Who says so: a Catalyst Center ("Catalyst Center dk", CDP/LLDP) or "Nautobot" (a cable).'
    )
    device_id: str | None = Field(
        None, description="The switch's Nautobot id, when a Nautobot cable says so."
    )


# Shared by the map and the circuits page.
class Circuit(Answer):
    """A circuit as Nautobot has it; empty text where Nautobot has nothing."""

    id: str
    cid: str = Field(description='"Unnamed circuit" when it has no circuit ID.')
    provider: str
    circuit_type: str
    status: str
    tenant: str
    commit_rate: str = Field(examples=["10 Gbps"], description="Empty when Nautobot has no speed.")
    description: str


# The one error model of the whole API (#604): defined beside the error handlers.
from app.core.exceptions import Error  # noqa: E402, F401
