"""Monitoring's DTOs (#594, #624): the liveness probe's answer."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app: Literal["ok"]
    database: Literal["ok", "unavailable"]


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "unavailable"]
    checks: HealthChecks
    inventory_sync_age_seconds: int | None = Field(
        None,
        description="Since the Nautobot inventory last synced; null when it never has.",
    )
