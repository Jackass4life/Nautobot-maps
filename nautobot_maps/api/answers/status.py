"""The tenant status page's answers (#449)."""

from typing import Literal

from pydantic import Field

from nautobot_maps.api.answers import common


class StatusSite(common.Answer):
    name: str
    state: Literal["outage", "degraded", "maintenance", "unknown", "operational"]
    since: str | None = Field(description="When an outage or degradation started (ISO 8601, UTC).")
    until: str | None = Field(description="When maintenance ends, as planned.")
    address: str | None = Field(description="The site's physical address in Nautobot, when it has one (#493).")
    latitude: float | None = Field(description="Where the site is, when Nautobot has its coordinates (#493).")
    longitude: float | None = Field(description="With ``latitude``, or both are null.")
    network_devices: int = Field(description="Its monitored devices that aren't access points (#503).")
    access_points: int = Field(description="Its monitored devices with a role in ACCESS_POINT_ROLES (#503).")
    network_devices_down: int = Field(description="Of ``network_devices``, down now; not those in maintenance.")
    access_points_down: int = Field(description="Of ``access_points``, down now; not those in maintenance.")


class StatusSummary(common.Answer):
    sites: int
    affected: int = Field(description="Sites in outage or degraded.")
    devices_down: int = Field(description="Devices down now at the affected sites (#503).")


class TenantStatus(common.Answer):
    tenant: str = Field(description="The tenant's name as Nautobot spells it.")
    description: str | None = Field(description="The tenant's description in Nautobot, when it has one (#482).")
    checked_at: str = Field(description="When the board was last built (ISO 8601, UTC).")
    summary: StatusSummary
    sites: list[StatusSite] = Field(description="Worst first, then by name.")
