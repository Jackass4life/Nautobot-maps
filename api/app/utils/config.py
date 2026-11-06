"""Application settings, read from the environment (and `.env`)."""

import ipaddress
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL, make_url

Role = Literal["viewer", "operator", "admin"]
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _csv(value: object) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return list(value)  # type: ignore[call-overload]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
    # Optional: the password on its own, as is (no URL encoding). It replaces the one in
    # DATABASE_URL, so a password with "@", ":" or "%" needs no escaping (#577).
    database_password: str | None = None
    cors_origins: list[str] = ["http://localhost:3000"]
    sql_echo: bool = False

    # Authentication (#582). Same names and meaning as the current app's settings.
    # disabled: no sign-in, everyone may do everything (development only).
    # header: a trusted sign-in proxy (oauth2-proxy) sets the user and group headers.
    auth_mode: Literal["disabled", "header"] = "disabled"
    auth_header_user: str = "X-Forwarded-User"
    auth_header_groups: str = "X-Forwarded-Groups"
    # Identity headers count only from these peer addresses (CSV of CIDRs) and, when
    # AUTH_PROXY_SECRET is set, only with it in X-Auth-Proxy-Secret.
    auth_trusted_proxies: Annotated[list[IpNetwork], NoDecode] = [
        ipaddress.ip_network("127.0.0.1/32"),
        ipaddress.ip_network("::1/128"),
    ]
    auth_proxy_secret: str = ""
    # Group names (CSV, case-insensitive) that give each role; the highest wins.
    auth_viewer_groups: Annotated[frozenset[str], NoDecode] = frozenset()
    auth_operator_groups: Annotated[frozenset[str], NoDecode] = frozenset()
    auth_admin_groups: Annotated[frozenset[str], NoDecode] = frozenset()
    # Role for a signed-in user in none of the groups; empty: no role.
    auth_default_role: Role | Literal[""] = ""
    # Reads need at least the viewer role too (otherwise reads are public).
    auth_require_viewer: bool = False

    @field_validator("auth_mode", "auth_default_role", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("auth_trusted_proxies", mode="before")
    @classmethod
    def _networks(cls, value: object) -> list[IpNetwork]:
        return [ipaddress.ip_network(str(part), strict=False) for part in _csv(value)]

    @field_validator(
        "auth_viewer_groups", "auth_operator_groups", "auth_admin_groups", mode="before"
    )
    @classmethod
    def _groups(cls, value: object) -> frozenset[str]:
        return frozenset(part.lower() for part in _csv(value))

    def sqlalchemy_url(self) -> URL:
        url = make_url(self.database_url)
        if self.database_password is not None:
            url = url.set(password=self.database_password)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
