"""API tokens (#297, #592): the current app's answers (nautobot_maps/api/answers/admin.py)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Token(Answer):
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


class Tokens(Answer):
    tokens: list[Token] = Field(
        description="Newest first; only the active ones unless all=true (#528)."
    )
    inactive: int = Field(description="How many of them are revoked or expired, listed or not.")


class TokensPurged(Answer):
    tokens: list[Token] = Field(
        description="The tokens deleted (or, with dry_run, that would be), newest first."
    )
    purged: int = Field(description="How many were deleted; 0 with dry_run.")
    dry_run: bool


class TokenCreated(Answer):
    token: NewToken


class TokenRevoked(Answer):
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
