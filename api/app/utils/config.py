"""Application settings, read from the environment (and `.env`)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/app"
    cors_origins: list[str] = ["http://localhost:3000"]
    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
