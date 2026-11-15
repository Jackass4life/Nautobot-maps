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
    role: Literal["viewer", "operator", "admin", "noc", "customer", "status", ""] = Field(
        description='The role it was made with; "" for a key made for an identity. A key\'s '
        "rights are its identity's (#719), whatever this says; status: a customer's token."
    )
    identity: str | None = Field(
        description="The identity it belongs to and whose rights it has (#719); null for a "
        "status token."
    )
    tenant: str | None = Field(description="The tenant a status token opens.")
    notify_email: str = Field(
        description="A status token's customer addresses for alarm and recovery mails "
        '(#707), comma-separated; "" for none.'
    )
    notify_after_minutes: int | None = Field(
        None,
        description="How long one of the tenant's sites is in alarm before those addresses "
        "get the mail (#729), in minutes; null: at once.",
    )
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
    role: str | None = Field(
        None,
        description="viewer, operator (NWO), admin, noc, customer (an identity of the key's own "
        "with that role, #719), or status with a tenant; or none, with identity",
    )
    identity: str | None = Field(
        None, description="A key for this existing identity, with exactly its rights (#719)."
    )
    tenant: str | None = None
    notify_email: str | None = Field(
        None,
        description="Role status only: up to 10 customer addresses, comma-separated, that "
        "get a mail when one of the tenant's sites goes into alarm or recovers (#707). "
        "Set once: to change them, revoke the token and make a new one.",
    )
    notify_after_minutes: int | None = Field(
        None,
        description="With notify_email: how long a site is in alarm before the mail, 0 to "
        "1440 minutes (#729); back up sooner, no mail. Unset: 0, at once.",
    )
    expires_in_days: int | None = None


# Identities (#715): /api/v1/identities, who may sign in.
class IdentityDto(common.Answer):
    """A person the app knows by the user name a sign-in gives."""

    unique_name: str = Field(description="The user name a sign-in gives, lowercase.")
    common_name: str = Field(description='Their name as people know it; "" when not set.')
    description: str
    roles: list[str] = Field(description="Their roles, the highest first.")
    scopes: list[str] = Field(description="Scopes of their own, besides their roles'.")
    keys: int = Field(description="Its API keys that work (not revoked, not expired), #721.")
    for_a_key: bool = Field(
        description="Made for one API key (``token create --role``), not for a person."
    )


class Identities(common.Answer):
    identities: list[IdentityDto] = Field(description="By name.")


class IdentityAnswer(common.Answer):
    identity: IdentityDto


class IdentityDeleted(common.Answer):
    deleted: str = Field(description="The name: it can no longer sign in as an identity.")


class IdentityCreate(BaseModel):
    """A new identity (#715)."""

    name: str = Field(description="The user name the sign-in gives (the proxy's).")
    roles: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list, description="e.g. maintenance:write")
    common_name: str = ""
    description: str = ""


class IdentityRole(BaseModel):
    role: str


class IdentityScope(BaseModel):
    scope: str = Field(description="e.g. alerts:ack, alerts:*")


class RoleDto(common.Answer):
    """A role: what it may do (its scopes) and who has it (#721)."""

    name: str
    label: str = Field(description="What the pages call it.")
    landing: str = Field(description="The page someone with it opens on, e.g. /noc.")
    description: str
    built_in: bool = Field(description="One of the five the app starts with: never deleted.")
    scopes: list[str] = Field(description="e.g. alerts:ack, alerts:*; * stands for any one part.")
    identities: int = Field(description="How many identities have it.")


class Roles(common.Answer):
    roles: list[RoleDto] = Field(description="The built-in ones first.")


class RoleAnswer(common.Answer):
    role: RoleDto


class RoleDeleted(common.Answer):
    deleted: str
    identities: int = Field(description="How many identities had it, and lost it.")


class RoleCreate(BaseModel):
    """A new role (#721)."""

    name: str = Field(description="a-z first, then a-z, 0-9, _ or -.")
    label: str = ""
    description: str = ""
    landing: str = Field("/", description="The page it opens on.")
    scopes: list[str] = Field(default_factory=list)


class RoleChange(BaseModel):
    """What changes: the fields given."""

    label: str | None = None
    description: str | None = None
    landing: str | None = None


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
