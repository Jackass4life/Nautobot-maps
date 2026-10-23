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


class Tokens(common.Answer):
    tokens: list[Token] = Field(description="Newest first.")


class TokenCreated(common.Answer):
    token: NewToken


class TokenRevoked(common.Answer):
    token: Token


class NotificationResults(common.Answer):
    results: dict[str, str] = Field(
        description='Per configured channel (webhook, teams, email): "ok" or the error, never a URL or password.'
    )
