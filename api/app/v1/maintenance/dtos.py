"""Maintenance's answers (#449)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.v1.common import dtos as common


class Window(common.Answer):
    """A maintenance window; times are ISO 8601, UTC."""

    id: int
    site_id: str
    device_id: str = Field(description="Empty for the whole site.")
    starts_at: str
    ends_at: str = Field(description="As planned.")
    reason: str
    created_by: str
    created_at: str
    ended_at: str | None = Field(
        description="When it was ended early or cancelled; null otherwise."
    )
    ended_by: str
    batch_id: str = Field(description="The same for windows planned together.")
    state: Literal["active", "upcoming", "ended", "cancelled"]


class DescribedWindow(Window):
    """A window with the names the page shows; left out of one site's list
    (``site_id``), whose panel knows them."""

    site_name: str | None = Field(None, description="Empty when the site isn't on the board.")
    site_path: str | None = None
    tenants: list[str] | None = None
    device_name: str | None = Field(None, description="Empty for the whole site.")


class WindowList(common.Answer):
    windows: list[DescribedWindow] = Field(description="Soonest first.")
    tenant_descriptions: dict[str, str] = Field(
        description="Tenant name to its description, when it has one."
    )
    truncated: bool = Field(description="More windows than the list holds.")


class HistoryPeriod(common.Answer):
    from_: str = Field(alias="from", examples=["2026-10-01"])
    to: str = Field(examples=["2026-10-30"])
    label: str
    tz: str = Field(examples=["Europe/Copenhagen"])


class MaintenanceHistory(common.Answer):
    windows: list[DescribedWindow] = Field(description="Newest first.")
    tenant_descriptions: dict[str, str]
    period: HistoryPeriod
    site_id: str
    truncated: bool = Field(
        description="More than 5,000 windows: choose a shorter period, a site or a tenant."
    )


class PlannableSite(common.Answer):
    id: str
    name: str
    path: str
    tenants: list[str]
    devices: int = Field(description="Monitored devices.")
    maintenance: bool = Field(description="In a window now.")


class PlannableSites(common.Answer):
    sites: list[PlannableSite]
    tenant_descriptions: dict[str, str] = Field(
        description="Tenant name to its description, when it has one."
    )


class MaintenanceDevice(common.Answer):
    id: str
    name: str
    role: str
    status: str
    location_path: str


class Devices(common.Answer):
    devices: list[MaintenanceDevice] = Field(description="By name.")


class Created(common.Answer):
    windows: list[Window] = Field(description="One per site, or per device.")


class Ended(common.Answer):
    windows: list[Window] = Field(description="Ones already over are returned as they were.")
    not_found: list[int]


class EndedOne(common.Answer):
    window: Window


# What the routes take (#436): query parameters and bodies. The values are
# checked here for their type and shape; the rules stay in the routes, with
# their own messages.
class MaintenanceList(common.Query):
    """``site_id``, ``all=1`` (ended and cancelled too) and ``recent_days``."""

    site_id: str | None = None
    all_: str | None = Field(None, alias="all")
    recent_days: str | None = None


class MaintenanceHistoryQuery(common.Query):
    """The history's period (``from``/``to``, both days included), the browser's
    ``tz``, and one ``site_id`` or ``tenant``."""

    from_: str | None = Field(None, alias="from")
    to: str | None = None
    tz: str | None = None
    site_id: str | None = None
    tenant: str | None = None


class MaintenanceDevices(common.Query):
    site_id: str | None = None
    include_non_operational: str | None = None


class MaintenanceCreate(BaseModel):
    """Put a site, or some of its devices, in maintenance (#283, #343)."""

    site_id: str | None = None
    site_ids: list[str] | None = Field(None, description="Up to 200 sites at once")
    reason: str
    device_ids: list[str] | None = Field(
        None, description="One site only; leave out for the whole site"
    )
    starts_at: str | None = Field(None, description="ISO-8601 with a time zone; default now")
    ends_at: str | None = None
    duration_minutes: int | None = None


class MaintenanceEnd(BaseModel):
    """End or cancel several windows (#343)."""

    ids: list[int]
