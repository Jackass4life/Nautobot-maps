"""Incoming alerts' answers (#449)."""

from typing import Literal

from pydantic import Field

from nautobot_maps.api.answers import common


class PushedDevice(common.Answer):
    device_id: int | None = Field(description="LibreNMS's ID.")
    hostname: str
    status: Literal["up", "down"]


class LibreNmsPush(common.Answer):
    updated: bool = Field(description="False for a device LibreNMS doesn't know: not an error, so it isn't retried.")
    device: PushedDevice | None


class WebhookResult(common.Answer):
    firing: int
    resolved: int
    skipped: int = Field(description="Alerts without a device label.")
    matched: int = Field(description="Firing alerts that name a Nautobot device; 0 hints at the wrong label.")
