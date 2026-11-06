"""The data contract's answers (#309, #598): the current app's answers/admin.py."""

from pydantic import BaseModel, ConfigDict, Field


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContractFieldDto(Answer):
    path: str
    types: list[str]
    required: bool
    becomes: str = Field(description="What the app makes of it.")
    rule: str


class ContractEndpointDto(Answer):
    source: str = Field(examples=["nautobot"])
    endpoint: str
    used_for: str
    request: str
    stored_in: str
    checked: bool = Field(description="Checked at every sync.")
    notes: list[str]
    fields: list[ContractFieldDto]


class MismatchDto(Answer):
    field: str
    problem: str
    count: int


class ContractCheckDto(Answer):
    source: str
    endpoint: str
    records: int
    mismatches: list[MismatchDto]
    checked_at: str | None = None


class ContractDto(Answer):
    contract: list[ContractEndpointDto] = Field(
        description="Per upstream endpoint, the fields read."
    )
    checks: list[ContractCheckDto] = Field(description="The last sync's check of each.")
