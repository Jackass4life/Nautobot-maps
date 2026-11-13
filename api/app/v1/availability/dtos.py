"""Availability's answers (#449)."""

from typing import Literal

from pydantic import Field

from app.v1.common import dtos as common

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


class AvailabilitySummary(common.Answer):
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
    network_devices: int = Field(
        description="Monitored devices that are not access points, in maintenance too."
    )
    access_points: int = Field(
        description="Monitored devices with a role in ACCESS_POINT_ROLES, in maintenance too."
    )
    downtime_seconds: int = Field(
        description="Down time outside maintenance; devices down together count once."
    )
    incidents: int
    outages: int
    longest_outage_seconds: int
    mean_outage_seconds: int | None = Field(
        description="Restored outages only; null when none was restored."
    )
    first_outage: str | None
    last_outage: str | None
    maintenance_seconds: int = Field(description="Time in a whole-site maintenance window.")
    availability: float = Field(description="Percent.", examples=[99.9])


class Report(common.Answer):
    month: str | None = Field(description="The month, when the period is one whole month.")
    basis: Basis
    period: Period
    summary: AvailabilitySummary
    sites: list[SiteRow] = Field(description="Lowest availability first.")
    tenants: list[TenantRow] = Field(description="Lowest availability first.")
    tenant_descriptions: dict[str, str] = Field(
        description="Tenant name to its description, when it has one."
    )
    status_links: dict[str, str] | None = Field(
        None,
        description="Tenant name to its status page link id: /status/<id> (#500), for the "
        "tenants that have one; only with the status.links.view permission (#687), "
        "absent otherwise.",
    )


# What the routes take (#436): query parameters and bodies. The values are
# checked here for their type and shape; the rules stay in the routes, with
# their own messages.
class PeriodQuery(common.Query):
    """A report's period (#287, #341): ``month`` (``2026-10``), or ``from`` and
    ``to`` (``2026-10-03``, both days included), and the ``basis``."""

    month: str | None = None
    from_: str | None = Field(None, alias="from")
    to: str | None = None
    basis: str | None = None


class AvailabilityCsv(PeriodQuery):
    """The CSV's period, which table (``view``) and whether a ``header`` goes above it."""

    view: str | None = None
    header: str | None = None
