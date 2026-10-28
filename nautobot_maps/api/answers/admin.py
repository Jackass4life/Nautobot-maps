"""Administration's answers (#449)."""

from typing import Literal

from pydantic import Field

from nautobot_maps.api.answers import common


class ContractField(common.Answer):
    path: str
    types: list[str]
    required: bool
    becomes: str = Field(description="What the app makes of it.")
    rule: str


class ContractEndpoint(common.Answer):
    source: str = Field(examples=["Nautobot"])
    endpoint: str
    used_for: str
    request: str
    stored_in: str
    checked: bool = Field(description="Checked at every sync.")
    notes: list[str]
    fields: list[ContractField]


class Mismatch(common.Answer):
    field: str
    problem: str
    count: int = Field(description="Records with the problem.")


class ContractCheck(common.Answer):
    source: str
    endpoint: str
    records: int
    mismatches: list[Mismatch]
    checked_at: str | None = None


class Contract(common.Answer):
    contract: list[ContractEndpoint] = Field(description="Per upstream endpoint, the fields read.")
    checks: list[ContractCheck] = Field(description="The last sync's check of each; empty without a database.")


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
        None, description="A status token: its tenant's page, /status/<id> (#500); send it as /status/<id>#key=<token>."
    )


class Tokens(common.Answer):
    tokens: list[Token] = Field(description="Newest first; only the active ones unless all=true (#528).")
    inactive: int = Field(description="How many of them are revoked or expired, listed or not.")


class TokensPurged(common.Answer):
    tokens: list[Token] = Field(description="The tokens deleted (or, with dry_run, that would be), newest first.")
    purged: int = Field(description="How many were deleted; 0 with dry_run.")
    dry_run: bool


class TokenCreated(common.Answer):
    token: NewToken


class TokenRevoked(common.Answer):
    token: Token


class StatusLink(common.Answer):
    tenant: str = Field(description="The tenant, as Nautobot spells it.")
    status_link: str = Field(
        description="Its status page, /status/<id> (#500); with a status token, send /status/<id>#key=<token>."
    )


class StatusLinkReplaced(StatusLink):
    message: str = Field(description="What changed: the old link stopped working; the tenant's tokens still work.")


class WirelessControllerCheck(common.Answer):
    controller: str = Field(description='Its name as the top bar says it, e.g. "Catalyst Center dk".')
    kind: Literal["catalyst_center", "aruba_central"]
    url: str
    reachable: bool = Field(description="Whether it answered at all.")
    signed_in: bool | None = Field(description="Whether it took the account; null when that is unknown.")
    access_points: int | None = Field(None, description="How many access points it lists.")
    down: int | None = Field(None, description="How many of them it reports down.")
    matched: int | None = Field(None, description="How many match a Nautobot device (serial, else name).")
    unmatched_examples: list[str] | None = Field(None, description="Up to 10 it lists that Nautobot doesn't have.")
    error: str | None = Field(None, description="Why it couldn't be read, in plain words; never a credential.")


class WirelessCheck(common.Answer):
    controllers: list[WirelessControllerCheck] = Field(description="One per instance in WIRELESS_CONTROLLERS_FILE.")
    duplicates: dict[str, list[str]] = Field(
        description="Up to 20 serials more than one controller lists, and which (the newest answer wins)."
    )
    duplicate_count: int


class NotificationResults(common.Answer):
    results: dict[str, str] = Field(
        description='Per configured channel (webhook, teams, email): "ok" or the error, never a URL or password.'
    )
