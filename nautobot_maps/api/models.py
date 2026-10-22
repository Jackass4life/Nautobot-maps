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


class MaintenancePage(Query):
    view: str | None = None


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


def flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes (``routes.common.flag_arg``)."""
    return (value or "").strip().lower() in {"1", "true", "yes"}
