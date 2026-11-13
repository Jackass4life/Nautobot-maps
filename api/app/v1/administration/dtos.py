"""The Admin page's DTOs (#449, #624): tenants, status page links, the wireless
check, test notifications, API tokens (#297, #592) and the data contract (#309, #598)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.v1.common import dtos as common


class Tenant(common.Answer):
    name: str
    description: str = Field(description="Its Nautobot description; empty when it has none.")


class Tenants(common.Answer):
    tenants: list[Tenant] = Field(description="Nautobot's tenants as of the last sync, by name.")


class WirelessControllerCheck(common.Answer):
    controller: str = Field(
        description='Its name as the top bar says it, e.g. "Catalyst Center dk".'
    )
    kind: Literal["catalyst_center", "aruba_central"]
    url: str
    reachable: bool = Field(description="Whether it answered at all.")
    signed_in: bool | None = Field(
        description="Whether it took the account; null when that is unknown."
    )
    access_points: int | None = Field(None, description="How many access points it lists.")
    down: int | None = Field(None, description="How many of them it reports down.")
    matched: int | None = Field(
        None, description="How many match a Nautobot device (serial, else name)."
    )
    unmatched_examples: list[str] | None = Field(
        None, description="Up to 10 it lists that Nautobot doesn't have."
    )
    error: str | None = Field(
        None, description="Why it couldn't be read, in plain words; never a credential."
    )


class WirelessCheck(common.Answer):
    controllers: list[WirelessControllerCheck] = Field(
        description="One per instance in WIRELESS_CONTROLLERS_FILE."
    )
    duplicates: dict[str, list[str]] = Field(
        description="Up to 20 serials more than one controller lists, and which (the newest answer wins)."
    )
    duplicate_count: int


class NotificationResults(common.Answer):
    results: dict[str, str] = Field(
        description='Per configured channel (webhook, teams, email): "ok" or the error, never a URL or password.'
    )


# API tokens (#297, #592): the new stack's /api/v1/tokens.
class Token(common.Answer):
    """An API token; never its secret."""

    id: int
    name: str
    prefix: str = Field(description="Its first characters, to recognise it.")
    role: Literal["viewer", "operator", "admin", "status"]
    tenant: str | None = Field(description="The tenant a status token opens.")
    created_by: str
    created_at: str
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    revoked_by: str
    state: Literal["active", "expired", "revoked"]


class NewToken(Token):
    token: str = Field(description="The secret: shown this once.")
    status_link: str | None = Field(
        None,
        description="A status token: its tenant's page, /status/<id> (#500); "
        "send it as /status/<id>#key=<token>.",
    )


class Tokens(common.Answer):
    tokens: list[Token] = Field(
        description="Newest first; only the active ones unless all=true (#528)."
    )
    inactive: int = Field(description="How many of them are revoked or expired, listed or not.")


class TokensPurged(common.Answer):
    tokens: list[Token] = Field(
        description="The tokens deleted (or, with dry_run, that would be), newest first."
    )
    purged: int = Field(description="How many were deleted; 0 with dry_run.")
    dry_run: bool


class TokenCreated(common.Answer):
    token: NewToken


class TokenRevoked(common.Answer):
    token: Token


class TokenCreate(BaseModel):
    """A new API token (#297)."""

    name: str
    role: str = Field(description="viewer, operator, admin, or status with a tenant")
    tenant: str | None = None
    expires_in_days: int | None = None


class TokenPurge(BaseModel):
    """Which tokens to delete for good (#528): revoked and expired ones, only those
    that stopped working more than ``older_than_days`` ago when given; ``dry_run``
    deletes nothing."""

    older_than_days: int | None = None
    dry_run: bool = False


# The data contract (#309, #598): /api/v1/contract.
class ContractFieldDto(common.Answer):
    path: str
    types: list[str]
    required: bool
    becomes: str = Field(description="What the app makes of it.")
    rule: str


class ContractEndpointDto(common.Answer):
    source: str = Field(examples=["nautobot"])
    endpoint: str
    used_for: str
    request: str
    stored_in: str
    checked: bool = Field(description="Checked at every sync.")
    notes: list[str]
    fields: list[ContractFieldDto]


class MismatchDto(common.Answer):
    field: str
    problem: str
    count: int


class ContractCheckDto(common.Answer):
    source: str
    endpoint: str
    records: int
    mismatches: list[MismatchDto]
    checked_at: str | None = None


class ContractDto(common.Answer):
    contract: list[ContractEndpointDto] = Field(
        description="Per upstream endpoint, the fields read."
    )
    checks: list[ContractCheckDto] = Field(description="The last sync's check of each.")
