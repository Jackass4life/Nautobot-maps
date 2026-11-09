from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ItemCreateDto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ItemDto(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    created_at: datetime
