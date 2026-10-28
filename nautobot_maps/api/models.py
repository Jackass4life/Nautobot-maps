"""What the FastAPI routes take, typed (#436): query parameters and bodies as
Pydantic models.  The values are checked here for their type and shape; the
rules (a valid month, a period of at most 366 days, ...) stay where they were,
with their own messages."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Query(BaseModel):
    """Query parameters as the Flask routes read them (``routes.common.arg``):
    stripped, and empty means not given.  Unknown ones are ignored."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _stripped(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class Period(Query):
    """A report's period (#287, #341): ``month`` (``2026-10``), or ``from`` and
    ``to`` (``2026-10-03``, both days included), and the ``basis``."""

    month: str | None = None
    from_: str | None = Field(None, alias="from")
    to: str | None = None
    basis: str | None = None


class AvailabilityCsv(Period):
    """The CSV's period, which table (``view``) and whether a ``header`` goes above it."""

    view: str | None = None
    header: str | None = None


class MaintenanceList(Query):
    """``site_id``, ``all=1`` (ended and cancelled too) and ``recent_days``."""

    site_id: str | None = None
    all_: str | None = Field(None, alias="all")
    recent_days: str | None = None


class MaintenanceHistory(Query):
    """The history's period (``from``/``to``, both days included), the browser's
    ``tz``, and one ``site_id`` or ``tenant``."""

    from_: str | None = Field(None, alias="from")
    to: str | None = None
    tz: str | None = None
    site_id: str | None = None
    tenant: str | None = None


class MaintenanceDevices(Query):
    site_id: str | None = None
    include_non_operational: str | None = None


# Request bodies, for the API spec (deps.body_schema); maintenance.create and
# the routes check them, with their own messages.
class MaintenanceCreate(BaseModel):
    """Put a site, or some of its devices, in maintenance (#283, #343)."""

    site_id: str | None = None
    site_ids: list[str] | None = Field(None, description="Up to 200 sites at once")
    reason: str
    device_ids: list[str] | None = Field(None, description="One site only; leave out for the whole site")
    starts_at: str | None = Field(None, description="ISO-8601 with a time zone; default now")
    ends_at: str | None = None
    duration_minutes: int | None = None


class MaintenanceEnd(BaseModel):
    """End or cancel several windows (#343)."""

    ids: list[int]


class LocationDetail(Query):
    location_type: str | None = None


class Scope(Query):
    """A page's sites (#364, #367): ``tenant`` (any of them) and ``q`` (every
    one of them), each repeatable."""

    tenant: list[str] = []
    q: list[str] = []


class Search(Query):
    """``q``: an address, a place, or ``lat,lon``."""

    q: str | None = None


class BoardQuery(Query):
    """``refresh=1`` (the board's ↻) and ``include_non_operational=1``."""

    refresh: str | None = None
    include_non_operational: str | None = None


class FeedQuery(Query):
    """The activity feed (#180): ``limit``, ``since``, ``kinds``, and a page's
    ``tenant`` / ``q`` (#369)."""

    limit: str | None = None
    since: str | None = None
    kinds: str | None = None
    tenant: list[str] = []
    q: list[str] = []


class HistoryQuery(Query):
    site_id: str | None = None
    device_id: str | None = None
    start_at: str | None = None
    end_at: str | None = None


class HistoryCsvQuery(Query):
    """A site's history as CSV (#277): ``site_id``, ``days`` and ``view``."""

    site_id: str | None = None
    days: str | None = None
    view: str | None = None


# Request bodies, for the API spec; the routes check them, with their own messages.
class CriticalityOverride(BaseModel):
    nautobot_device_id: str
    is_critical: bool = True
    reason: str | None = None
    updated_by: str | None = None


class Acknowledge(BaseModel):
    """Acknowledge (or remove it) for a site's open alarms (#288)."""

    site_id: str
    device_ids: list[str] | None = Field(None, description="Default: every device with an open alert")
    note: str | None = None


class AlertCase(BaseModel):
    """Attach a case number to devices' open alerts, all or nothing."""

    site_id: str
    case_number: str
    device_ids: list[str] | None = None
    device_id: str | None = None


class LibreNmsPush(BaseModel):
    """A LibreNMS alert push (#284): which device; also as a form or in the query string."""

    device_id: int | str | None = None
    hostname: str | None = None


class AlertmanagerWebhook(BaseModel):
    """Prometheus Alertmanager's webhook body (#292); ``alerts`` is what is read."""

    model_config = ConfigDict(extra="allow")

    version: str | None = None
    alerts: list[dict]


class DeviceStatus(BaseModel):
    """One device's status from any tool (#292); a list of these works too."""

    device: str
    status: str = Field(description='"down" or "up"')
    source: str | None = None
    id: str | None = None
    summary: str | None = None
    started_at: str | None = None


class TokenList(Query):
    """``all``: revoked and expired tokens too (#528); ``tenant``: only its status tokens."""

    all: str | None = None
    tenant: str | None = None


class TokenPurge(BaseModel):
    """Which tokens to delete for good (#528): revoked and expired ones, only
    those that stopped working more than ``older_than_days`` ago when given;
    ``dry_run`` deletes nothing."""

    older_than_days: int | None = None
    dry_run: bool = False


class TokenCreate(BaseModel):
    """A new API token (#297)."""

    name: str
    role: str = Field(description="viewer, operator, admin, or status with a tenant")
    tenant: str | None = None
    expires_in_days: int | None = None


def flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes (``routes.common.flag_arg``)."""
    return (value or "").strip().lower() in {"1", "true", "yes"}
