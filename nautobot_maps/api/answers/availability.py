"""Availability's answers (#449)."""

from typing import Literal

from pydantic import Field

from nautobot_maps.api.answers import common

Basis = Literal["any_down", "critical"]


class Period(common.Answer):
    start: str = Field(description="ISO 8601, UTC.")
    end: str = Field(description="ISO 8601, UTC; now, for a period still going on.")
    seconds: int
    complete: bool = Field(description="False while the period is still going on.")
    from_: str = Field(alias="from", examples=["2026-10-01"])
    to: str = Field(examples=["2026-10-31"])
    label: str = Field(examples=["2026-10"])


class TenantRow(common.Answer):
    tenant: str
    sites: int
    availability: float = Field(description="Percent, the mean of its sites'.", examples=[99.875])
    downtime_seconds: int


class Summary(common.Answer):
    sites: int
    availability: float = Field(description="Percent, every site counting the same.")
    downtime_seconds: int
    incidents: int
    below_target: int
    no_downtime: int
    target: float = Field(description="The target, in percent.")
    worst_tenant: TenantRow | None = Field(description="Null when no tenant had downtime.")


class SiteRow(common.Answer):
    site_id: str
    name: str
    tenants: list[str]
    path: str = Field(description="Its place in the location tree.")
    address: str
    country: str
    tier: str
    monitored_devices: int
    downtime_seconds: int = Field(description="Down time outside maintenance; devices down together count once.")
    incidents: int
    outages: int
    longest_outage_seconds: int
    mean_outage_seconds: int | None = Field(description="Restored outages only; null when none was restored.")
    first_outage: str | None
    last_outage: str | None
    maintenance_seconds: int = Field(description="Time in a whole-site maintenance window.")
    availability: float = Field(description="Percent.", examples=[99.9])


class Report(common.Answer):
    month: str | None = Field(description="The month, when the period is one whole month.")
    basis: Basis
    period: Period
    summary: Summary
    sites: list[SiteRow] = Field(description="Lowest availability first.")
    tenants: list[TenantRow] = Field(description="Lowest availability first.")
    tenant_descriptions: dict[str, str] = Field(description="Tenant name to its description, when it has one.")
