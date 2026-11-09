"""The alert board's answers (#449)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.v1.common import dtos as common

Level = Literal["critical", "medium", "low", "ok", "maintenance", "no_data"]


class Acknowledged(common.Answer):
    by: str
    at: str
    note: str


class Stack(common.Answer):
    """The stack (virtual chassis) the device is part of."""

    name: str
    position: int | None
    size: int
    master: bool
    whole: bool = Field(description="Every counted member is down.")
    members: list[str] | None = Field(
        None, description="The members' names, when the whole stack is down."
    )


class Behind(common.Answer):
    device: str = Field(description="The switch most of the site's down access points are on.")
    down: int = Field(description="How many access points on it are down (at least 2).")
    total: int = Field(description="How many access points are on it.")


class DownDevice(common.Answer):
    """A device with an open alert (also one in maintenance, marked so)."""

    device_id: str
    device_name: str
    device_ip: str
    status: str
    role: str
    case_numbers: list[str]
    down_started_at: str | None = Field(
        None, description="When it went down; from the alert history."
    )
    acknowledged: Acknowledged | None = Field(None, description="Someone is on it.")
    location_path: str | None = Field(
        None, description="Where below the site, when rows are rolled up."
    )
    flapping: bool | None = None
    flap_changes: int | None = None
    down_reason: str | None = Field(
        None, description="Who says it is down, and why, when not Nautobot."
    )
    stack: Stack | None = None
    set_level: str | None = Field(None, description="A level set on the device in Nautobot.")
    maintenance_until: str | None = None
    maintenance_reason: str | None = None
    core: bool | None = Field(None, description="The likely cause of the others; listed first.")
    probable_cause: str | None = Field(
        None, description="The core device(s) it is likely down because of."
    )
    access_point: bool | None = Field(
        None, description="An access point a wireless controller watches (#521)."
    )
    neighbour: common.Neighbour | None = Field(
        None, description="The switch and port this access point is on (#524)."
    )


class TenantAlert(common.Answer):
    alert_level: Level
    device_count: int
    current_downtime_seconds: int
    # Its own devices (#503); 0 in a board cached by the release before.
    access_point_count: int = 0
    network_device_count: int = 0
    down_access_point_count: int = 0
    down_network_device_count: int = 0
    wireless_only: bool = Field(
        False, description="Only access points down, per a wireless controller (#521)."
    )


class TenantDevice(common.Answer):
    """One of a tenant's own devices, as its status page names it (#555)."""

    name: str = Field(description="The device's name; a stack's name for a stack.")
    state: Literal["up", "down", "maintenance"]
    since: str | None = Field(description="When it went down (ISO 8601, UTC); null unless down.")


class SiteMaintenance(common.Answer):
    until: str
    reason: str
    by: str


class BoardSite(common.Answer):
    """A site (one row of the board): the location as stored, and its alarm."""

    id: str
    name: str
    slug: str
    status: str
    location_type: str
    parent: str
    parent_id: str
    ancestor_path: str
    latitude: float | None
    longitude: float | None
    description: str
    physical_address: str
    country: str
    facility: str
    tenant: str
    tenant_id: str
    tenant_group: str
    asn: int | None
    time_zone: str
    tags: list[str]
    url: str
    last_updated: str | None = Field(None, description="Read live from Nautobot only.")
    tenant_descriptions: dict[str, str]
    tenants: list[str] = Field(description="Its own tenant first, then those linked to it.")
    tenant_alerts: dict[str, TenantAlert] = Field(
        description="TENANT_SCOPE_FIELD: the level for each tenant with its own device roles (lower-case name)."
    )
    tenant_devices: dict[str, list[TenantDevice]] = Field(
        default_factory=dict,
        description="STATUS_PAGE_TENANT_DEVICES (#555): the devices whose Nautobot tenant is each of the site's "
        "tenants (lower-case name), for their status page; absent without the setting.",
    )
    alert_level: Level
    alert_reason: str
    wireless_only: bool = Field(
        False,
        description="Only access points down, per a wireless controller, at WIRELESS_AP_LEVEL (#521): "
        "the Wireless chip, not an alarm, never notified.",
    )
    tier: str = Field(description="A tier in SITE_TIERS; empty otherwise.")
    tier_rank: int = Field(description="0 for the most important tier; 99 without one.")
    device_count: int = Field(description="Monitored devices, not those in maintenance.")
    maintenance_device_count: int
    down_device_count: int
    # Not required: a board cached by the release before (#499) has neither,
    # and a missing one must not make /api/alerts fail until the next build.
    access_point_count: int = Field(
        0,
        description="Monitored devices with a role in ACCESS_POINT_ROLES or listed by a wireless controller, "
        "in maintenance too.",
    )
    network_device_count: int = Field(
        0, description="The other monitored devices, in maintenance too."
    )
    down_access_point_count: int = Field(
        0, description="Of the access points, down now; not those in maintenance (#503)."
    )
    down_network_device_count: int = Field(
        0, description="Of the others, down now; not those in maintenance (#503)."
    )
    current_downtime_seconds: int = Field(description="Its longest current outage.")
    latest_down_at: str | None = Field(description="When its newest open alert started.")
    historical_downtime_seconds: int
    active_alert_instance_count: int
    active_cases: list[str]
    down_devices: list[DownDevice] = Field(description="The likely cause first.")
    behind: Behind | None = Field(
        None,
        description='"12 of 16 behind sw02" (#524): two or more of its down access points are on one switch.',
    )
    maintenance: SiteMaintenance | None = Field(
        None, description="The whole site is in a maintenance window."
    )


class BoardSummary(common.Answer):
    total: int
    critical: int
    medium: int
    low: int
    no_data: int
    ok: int
    maintenance: int
    wireless: int = Field(
        0, description="Sites with only access points down (#521); not in the levels or non_ok."
    )
    non_ok: int = Field(description="critical + medium + low.")


class Board(common.Answer):
    checked_at: str = Field(description="When the board was built (ISO 8601, UTC).")
    stale_after_seconds: int
    summary: BoardSummary
    alerts: list[BoardSite] = Field(
        description="Worst first, then the more important tier, then most down."
    )
    next_maintenance_change: str | None = Field(description="When a window next starts or ends.")
    age_seconds: int
    stale: bool
    sync_pending: bool = Field(description="A sync is running; poll again.")
    persistence_configured: bool = Field(
        description="False: no database, so the board is always empty."
    )
    next_update_in_seconds: int | None = Field(
        description="Until the next sync is due; 0 = now, null = unknown."
    )


class FeedEvent(common.Answer):
    kind: Literal["down", "up", "severity"]
    at: str
    site_id: str
    site_name: str
    device_id: str = Field(description="Empty for a severity change.")
    device_name: str
    level: str
    down_since: str | None = Field(None, description="For down: when it went down.")
    from_level: str | None = Field(None, description="For severity.")
    to_level: str | None = Field(None, description="For severity.")


class Feed(common.Answer):
    events: list[FeedEvent] = Field(description="Newest first.")
    persistence_configured: bool
    matching_sites: int | None = Field(
        None, description="With tenant= or q=: how many sites match."
    )


class HistoryEvent(common.Answer):
    event_type: str
    event_at: str
    alert_level: str
    alert_reason: str
    snapshot: dict[str, Any] = Field(description="The device and site as they were.")


class HistoryCase(common.Answer):
    case_number: str
    created_by: str
    created_at: str


class Incident(common.Answer):
    id: int
    alert_key: str
    site_id: str
    site_name: str
    device_id: str
    device_name: str
    alert_level: str
    alert_reason: str
    status: Literal["open", "resolved"]
    down_started_at: str
    last_seen_down_at: str
    resolved_at: str | None
    total_downtime_seconds: int
    created_at: str
    updated_at: str
    probable_cause: str
    events: list[HistoryEvent]
    cases: list[HistoryCase] = Field(description="Newest first.")


class AlertHistory(common.Answer):
    instances: list[Incident] = Field(description="Newest down first, at most 500.")


class Override(common.Answer):
    nautobot_device_id: str
    is_critical: int = Field(description="1: critical; 0: not.")
    reason: str
    updated_by: str
    updated_at: str


class Overrides(common.Answer):
    overrides: list[Override] = Field(description="Newest first.")


class OverrideSet(common.Answer):
    status: Literal["ok"]
    nautobot_device_id: str
    is_critical: bool


class OverrideDeleted(common.Answer):
    status: Literal["deleted"]
    nautobot_device_id: str


class AckChanged(common.Answer):
    site_id: str
    changed: list[str] = Field(description="The devices whose acknowledgement changed.")


class CaseLink(common.Answer):
    device_id: str
    alert_instance_id: int


class CaseAdded(common.Answer):
    status: Literal["ok"]
    site_id: str
    case_number: str
    linked: list[CaseLink]
    device_id: str | None = Field(
        None, description="For one device only, as the API first answered."
    )
    alert_instance_id: int | None = Field(None, description="For one device only.")


# What the routes take (#436): query parameters and bodies. The values are
# checked here for their type and shape; the rules stay in the routes, with
# their own messages.
class BoardQuery(common.Query):
    """``refresh=1`` (the board's ↻) and ``include_non_operational=1``."""

    refresh: str | None = None
    include_non_operational: str | None = None


class FeedQuery(common.Query):
    """The activity feed (#180): ``limit``, ``since``, ``kinds``, and a page's
    ``tenant`` / ``q`` (#369)."""

    limit: str | None = None
    since: str | None = None
    kinds: str | None = None
    tenant: list[str] = []
    q: list[str] = []


class HistoryQuery(common.Query):
    site_id: str | None = None
    device_id: str | None = None
    start_at: str | None = None
    end_at: str | None = None


class HistoryCsvQuery(common.Query):
    """A site's history as CSV (#277): ``site_id``, ``days`` and ``view``."""

    site_id: str | None = None
    days: str | None = None
    view: str | None = None


class CriticalityOverride(BaseModel):
    nautobot_device_id: str
    is_critical: bool = True
    reason: str | None = None
    updated_by: str | None = None


class Acknowledge(BaseModel):
    """Acknowledge (or remove it) for a site's open alarms (#288)."""

    site_id: str
    device_ids: list[str] | None = Field(
        None, description="Default: every device with an open alert"
    )
    note: str | None = None


class AlertCase(BaseModel):
    """Attach a case number to devices' open alerts, all or nothing."""

    site_id: str
    case_number: str
    device_ids: list[str] | None = None
    device_id: str | None = None
