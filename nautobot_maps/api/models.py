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


def flag(value: str | None) -> bool:
    """A yes/no query value: 1, true or yes (``routes.common.flag_arg``)."""
    return (value or "").strip().lower() in {"1", "true", "yes"}
