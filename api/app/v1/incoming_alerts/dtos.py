"""Incoming alerts' answers (#449)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.v1.common import dtos as common


class PushedDevice(common.Answer):
    device_id: int | None = Field(description="LibreNMS's ID.")
    hostname: str
    status: Literal["up", "down"]


class LibreNmsPush(common.Answer):
    updated: bool = Field(
        description="False for a device LibreNMS doesn't know: not an error, so it isn't retried."
    )
    device: PushedDevice | None


class WebhookResult(common.Answer):
    firing: int
    resolved: int
    skipped: int = Field(
        description="Alerts left out: no device label, no status, or not in ALERTMANAGER_ALERTNAMES."
    )
    matched: int = Field(
        description="Firing alerts that name a Nautobot device; 0 hints at the wrong label."
    )


# What the routes take (#436): query parameters and bodies. The values are
# checked here for their type and shape; the rules stay in the routes, with
# their own messages.
class LibreNmsPushBody(BaseModel):
    """A LibreNMS alert push (#284): which device; also as a form or in the query string."""

    # Its name in the spec, as before (#624): "LibreNmsPush" is also the answer's.
    model_config = ConfigDict(title="LibreNmsPush")

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
