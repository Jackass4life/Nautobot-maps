"""The map's answers (#449)."""

from typing import Any, Literal

from pydantic import Field

from nautobot_maps.api.answers import circuits, common

Level = Literal["critical", "medium", "low", "ok", "maintenance", "no_data"]


class Location(common.Answer):
    """A Nautobot location, as stored at the last sync (or read live without
    a database); empty text where Nautobot has nothing."""

    id: str
    name: str
    slug: str
    status: str
    location_type: str
    parent: str
    parent_id: str
    ancestor_path: str = Field(examples=["EMEA › DNK"])
    latitude: float
    longitude: float
    description: str
    physical_address: str
    country: str
    tier: str
    tenant_scopes: dict[str, Any] = Field(description="TENANT_SCOPE_FIELD: tenant to the device roles that count.")
    facility: str
    tenant: str
    tenant_id: str
    tenant_group: str
    asn: int | None
    time_zone: str
    tags: list[str]
    url: str
    tenants: list[str] | None = Field(None, description="Its tenant and the ones linked to it; with a database.")
    last_updated: str | None = Field(None, description="Read live from Nautobot only.")
    tenant_descriptions: dict[str, str] = Field(description="Tenant name to its description, when it has one.")


class Locations(common.Answer):
    locations: list[Location] = Field(description="Those with coordinates, by name.")


class NearbyLocation(Location):
    distance_km: float


class Search(common.Answer):
    search_lat: float
    search_lon: float
    radius_km: int
    count: int
    locations: list[NearbyLocation] = Field(description="Within radius_km, nearest first.")


class LocationLevel(common.Answer):
    level: Level
    reason: str
    down: int
    monitored: int
    since: str | None = Field(None, description="When its newest open alert started.")
    until: str | None = Field(None, description="When its maintenance ends.")


class LocationAlerts(common.Answer):
    levels: dict[str, LocationLevel] = Field(
        description="Location ID to its level; a location left out has no monitored devices."
    )
    checked_at: str
    next_maintenance_change: str | None = Field(description="When a window next starts or ends.")


class CoordinateProblem(common.Answer):
    site_id: str
    name: str
    ancestor_path: str
    type: str
    status: str
    latitude: float | None
    longitude: float | None
    problem: Literal["invalid", "zero", "swapped", "missing", "far"]
    reason: str
    km_away: int | None = Field(description="How far from the rest of its region, for far and swapped.")
    compared_with: str = Field(description="The region it was compared with.")
    tenants: list[str]


class CoordinateCounts(common.Answer):
    invalid: int
    zero: int
    swapped: int
    missing: int
    far: int


class CoordinateCheck(common.Answer):
    counts: CoordinateCounts
    sites: list[CoordinateProblem] = Field(description="Worst first: invalid, zero, swapped, missing, far.")


class Device(common.Answer):
    """A device at the site, as the panel shows it."""

    id: str
    name: str
    device_type: str
    manufacturer: str
    role: str
    status: str = Field(description='"offline" when LibreNMS or a webhook says it is down.')
    primary_ip: str
    platform: str
    serial: str
    tenant: str
    last_updated: str
    criticality: str
    vc_id: str = Field(description="Its stack (virtual chassis); empty when none.")
    vc_name: str
    vc_position: int | None
    vc_master: bool
    core: bool = Field(description="Core device: likely the cause when the site is down.")
    level: str = Field(description="Its weight when it is down: critical, medium or low.")
    not_monitored: str = Field(description="Why it isn't counted; empty when it is.")
    location_id: str | None = Field(None, description="Read live from Nautobot only.")
    location_path: str | None = Field(None, description="Where below the site it is, when rows are rolled up.")
    librenms_hostname: str | None = None
    librenms_ip: str | None = None
    down_source: str | None = Field(None, description="Who says it is down, when Nautobot doesn't.")
    webhook_since: str | None = None
    webhook_summary: str | None = None
    maintenance_until: str | None = Field(None, description="In a maintenance window until then.")
    alert_pending: bool | None = Field(None, description="Down, within the alert delay.")


class Asn(common.Answer):
    asn: int | None
    description: str
    tenant: str


class SiteCircuit(circuits.Circuit):
    """A circuit at the site: terminated there, or (``match``) at its address."""

    term_side: str | None = None
    port_speed: str | None = None
    upstream_speed: str | None = None
    xconnect_id: str | None = None
    pp_info: str | None = None
    match: Literal["address"] | None = Field(None, description="Found by the address, not a termination.")
    shared_with: int | None = Field(None, description="Other sites at that address.")


class SiteAlert(common.Answer):
    level: Level
    reason: str


class SiteCounts(common.Answer):
    down: int | None = None
    monitored: int | None = None


class LocationDetail(common.Answer):
    devices: list[Device]
    alert: SiteAlert = Field(description="The level the board and the map show.")
    counts: SiteCounts
    asns: list[Asn]
    circuits: list[SiteCircuit]
    circuits_error: str = Field(description="Why circuits couldn't be read; empty when they were.")
