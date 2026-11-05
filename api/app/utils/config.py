"""Application settings, read from the environment (and `.env`)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
    # Optional: the password on its own, as is (no URL encoding). It replaces the one in
    # DATABASE_URL, so a password with "@", ":" or "%" needs no escaping (#577).
    database_password: str | None = None
    cors_origins: list[str] = ["http://localhost:3000"]
    sql_echo: bool = False

    def sqlalchemy_url(self) -> URL:
        url = make_url(self.database_url)
        if self.database_password is not None:
            url = url.set(password=self.database_password)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
