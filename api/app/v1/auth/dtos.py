from typing import Literal

from pydantic import BaseModel, Field

from app.v1.common.dtos import Access, Answer


class MeDto(BaseModel):
    """Who the API sees: `username` is null when not signed in, `role` null without one."""

    username: str | None
    role: str | None
    groups: list[str]
    auth_mode: Literal["disabled", "header"]
    access: Access
    # From access.permissions (#693): alerts:ack and admin.
    can_write: bool = Field(
        deprecated=True, description="Deprecated: alerts:ack in access.permissions."
    )
    can_administer: bool = Field(
        deprecated=True, description="Deprecated: admin in access.permissions."
    )


# The app's own sign-in (#717; the example's v1/auth/dtos.py).
class Jwt(Answer):
    """A sign-in token's claims (the example's Jwt): iss, aud, jti, sub (the user
    name), exp, nbf, iat; scopes and common_name for the reader. The API reads the
    identity's roles itself on every request."""

    iss: str
    aud: str
    jti: str
    sub: str
    exp: int
    nbf: int
    iat: int
    scopes: list[str]
    common_name: str | None = None


class AccessTokenResponseDto(Answer):
    access_token: str = Field(
        description="For Authorization: Bearer; the page signs in with the cookie set with it."
    )
    token_type: Literal["Bearer"]
    expires_in: int = Field(description="Seconds it is valid for.")


class SignInDto(BaseModel):
    """AD user name (``jdoe``, ``DOMAIN\\jdoe`` or ``jdoe@domain``) and password; as a
    form (OAuth2's password form, as the example) or JSON."""

    username: str
    password: str


class SignedOutDto(Answer):
    signed_out: bool
