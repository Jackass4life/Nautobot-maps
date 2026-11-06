import os

# Its own variable: TEST_DATABASE_URL is the current app's test database (#588).
TEST_DATABASE_URL = os.environ.get(
    "API_TEST_DATABASE_URL",
    "postgresql+psycopg://nautobot_maps:nautobot_maps@localhost:5432/fastapi_nuxt_test",
)


def _provision(url: str) -> None:
    """Create the test database when it doesn't exist yet (an existing devcontainer
    volume has only the current app's): one connection to the server's postgres DB."""
    import psycopg
    from sqlalchemy import make_url

    target = make_url(url)
    try:
        with psycopg.connect(
            host=target.host,
            port=target.port or 5432,
            user=target.username,
            password=target.password,
            dbname="postgres",
            autocommit=True,
            connect_timeout=5,
        ) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target.database,)
            ).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{target.database}"')
    except psycopg.Error:
        pass  # no server, or no rights: the migration fixture says what is wrong


_provision(TEST_DATABASE_URL)
# Must be set before `app` is imported: the engine is built from settings at import.
# Both names: NAUTOBOT_MAPS_DATABASE_URL wins over DATABASE_URL.
os.environ["NAUTOBOT_MAPS_DATABASE_URL"] = os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402
from app.session import engine  # noqa: E402
from app.utils.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = Config("alembic.ini")
    # ConfigParser interpolation: escape "%" in percent-encoded credentials.
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def configure(monkeypatch):
    """Set settings through the environment, e.g. configure(AUTH_MODE="header")."""

    def apply(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    return apply
