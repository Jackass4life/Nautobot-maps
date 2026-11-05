import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://nautobot_maps:nautobot_maps@localhost:5432/fastapi_nuxt_test",
)
# Must be set before `app` is imported: the engine is built from settings at import.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.main import app  # noqa: E402
from app.session import engine  # noqa: E402


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
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE items RESTART IDENTITY"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
