"""Monitoring's answers (#449)."""

from typing import Any, Literal

from pydantic import Field

from nautobot_maps.api.answers import common


class HealthChecks(common.Answer):
    app: Literal["ok"]
    database: Literal["ok", "unavailable"] | None = Field(None, description="Only when a database is configured.")


class Health(common.Answer):
    status: Literal["ok", "unavailable"]
    checks: HealthChecks
    inventory_sync_age_seconds: int | None = Field(
        description="Since the Nautobot inventory last synced; null when it never has or there is no database."
    )


class Source(common.Answer):
    name: str = Field(examples=["Nautobot"])
    state: Literal["ok", "stale", "down"]
    message: str = Field(examples=["Nautobot is up to date"])
    last_success: str | None = Field(None, description="ISO 8601, UTC; null before the first good sync.")


class AppStatus(common.Answer):
    state: Literal["ok", "stale", "down"] = Field(description="The worst of the sources.")
    updated_at: str | None = Field(description="The oldest source's last good sync (ISO 8601, UTC).")
    sources: list[Source]


class Endpoint(common.Answer):
    path: str = Field(examples=["/api/alerts"])
    methods: list[str]
    group: Literal["Pages", "API", "Monitoring"]
    summary: str
    description: str
    path_params: list[str]
    query_params: list[str]
    required_role: Literal["viewer", "operator", "admin"] | None
    example_body: dict[str, Any] | None
    example_body_text: str = Field(description="example_body, formatted; {} when there is none.")


class Endpoints(common.Answer):
    endpoints: list[Endpoint]
