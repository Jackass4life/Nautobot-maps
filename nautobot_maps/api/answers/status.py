"""The tenant status page's answers (#449)."""

from typing import Literal

from pydantic import Field

from nautobot_maps.api.answers import common


class StatusSite(common.Answer):
    name: str
    state: Literal["outage", "degraded", "maintenance", "unknown", "operational"]
    since: str | None = Field(description="When an outage or degradation started (ISO 8601, UTC).")
    until: str | None = Field(description="When maintenance ends, as planned.")


class StatusSummary(common.Answer):
    sites: int
    affected: int = Field(description="Sites in outage or degraded.")


class TenantStatus(common.Answer):
    tenant: str = Field(description="The tenant's name as Nautobot spells it.")
    checked_at: str = Field(description="When the board was last built (ISO 8601, UTC).")
    summary: StatusSummary
    sites: list[StatusSite] = Field(description="Worst first, then by name.")
