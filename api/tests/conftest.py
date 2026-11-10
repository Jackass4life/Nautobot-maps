import os
import sys
from pathlib import Path

# The demo's mock Nautobot (demo/mock_nautobot.py), for the tests ported from the current app.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "demo"))

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
# No background scheduler from the app's startup: it would tick against the database while
# tests empty it (the current app's tests never started gunicorn's). Its own tests turn it on.
os.environ["BACKGROUND_SYNC_ENABLED"] = "false"

import logging  # noqa: E402

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from loguru import logger  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.api import app  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.logs import config as logs  # noqa: E402
from app.models import Base  # noqa: E402


def _to_standard_logging(message) -> None:
    """loguru's lines into Python's standard logging, so ``caplog`` sees them (#633):
    under the line's logger name, only if that logger is enabled for its level."""
    record = message.record
    name = record["extra"]["logger"]
    target = logging.getLogger(name)
    if not target.isEnabledFor(record["level"].no):
        return
    exception = record["exception"]
    exc_info = (exception.type, exception.value, exception.traceback) if exception else None
    target.handle(
        target.makeRecord(
            name,
            record["level"].no,
            record["file"].path,
            record["line"],
            record["message"],
            (),
            exc_info,
            func=record["function"],
            extra={"from_loguru": True, "loguru_extra": dict(record["extra"])},
        )
    )


def _loguru_for_tests() -> None:
    logger.remove()
    logger.configure(patcher=logs.name_and_part)
    logger.add(_to_standard_logging, level=0, format="{message}", catch=False)


_REAL_CONFIGURE = logs.configure


@pytest.fixture(autouse=True)
def _loguru_to_caplog(monkeypatch):
    """Each test starts with loguru feeding the standard logging. logs.configure() (the
    CLI, gunicorn_config) does nothing, so caplog keeps its handler; the
    ``configure_logs`` fixture runs the real one."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    _loguru_for_tests()
    monkeypatch.setattr(logs, "configure", lambda: None)
    yield
    root.handlers[:], root.level = handlers, level
    logs._configured = False
    _loguru_for_tests()


@pytest.fixture
def configure_logs():
    """The real logs.configure(): loguru's sinks on sys.stderr/sys.stdout as they are
    when it is called (capsys's, in a test)."""
    return _REAL_CONFIGURE


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
    # A sync or rebuild a test started must end first: emptying the tables under it is a
    # deadlock (whatever order pytest tears the fixtures down in). One still running after
    # the wait fails the test, the tables left as they are (#603 review).
    running = join_background_threads()
    if running:
        pytest.fail(f"background threads still running, tables not emptied: {running}")
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
        from app.core import settings as config

        for key, value in env.items():
            monkeypatch.setenv(key, value)
            # Set here explicitly: a value a ported test left set in code doesn't win.
            config.OVERRIDES.pop(key.lower(), None)
        get_settings.cache_clear()

    return apply


class TestDatabase:
    """Direct access to the test database, for seeding and checking rows: the current
    app's tests/conftest.py helper, for the tests ported from it (#600, #602)."""

    __test__ = False  # not a test class

    def execute(self, sql: str, params=()) -> list[dict]:
        """Run one statement; return its rows as dicts (``[]`` when it returns none)."""
        from app.db import connection as db

        conn = db.get_conn()
        try:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []
        finally:
            conn.close()

    def executemany(self, sql: str, rows) -> None:
        from app.db import connection as db

        conn = db.get_conn()
        try:
            conn.cursor().executemany(sql, list(rows))
        finally:
            conn.close()


def join_background_threads(timeout: float = 30.0) -> list[str]:
    """Wait for background syncs and rebuilds a test started, so none runs into the next
    test (as the current app's conftest). Returns the ones still running."""
    import threading

    from app.services import inventory_service, scheduler

    names = {
        inventory_service.SYNC_THREAD_NAME,
        scheduler.PUSH_REBUILD_THREAD_NAME,
        scheduler.WIRELESS_THREAD_NAME,
    }
    running = []
    for thread in threading.enumerate():
        if thread.name in names and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                running.append(thread.name)
    return running


@pytest.fixture(autouse=True)
def _no_background_threads_left():
    yield
    running = join_background_threads()
    if running:
        pytest.fail(f"Background threads still running after the test: {running}")


@pytest.fixture
def pg_database():
    """The current app's per-test database, for tests ported from it: here the test
    database (emptied after each test by ``clean_tables``), with its helper."""
    from app.clients import caching

    caching.cache.clear()
    yield TestDatabase()
    join_background_threads()
    caching.cache.clear()


@pytest.fixture
def board(pg_database, monkeypatch):
    """A board built from the database only (no Nautobot, no LibreNMS), at a clock the test
    sets: ``board["now"]`` (the current app's fixture)."""
    from app.core.legacy_settings import settings
    from app.services import inventory_service
    from app.utils import timeutil

    clock = {"now": "2026-10-18T12:00:00Z"}
    monkeypatch.setattr(timeutil, "iso_utc_now", lambda: clock["now"])
    monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
    monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
    monkeypatch.setattr(settings, "LIBRENMS_URL", "")
    monkeypatch.setattr(settings, "NOTIFY_WEBHOOK_URL", "https://hooks.example.com/x")
    monkeypatch.setattr(settings, "NOTIFY_MIN_LEVEL", "critical")
    monkeypatch.setattr(inventory_service, "ensure_snapshot", lambda *args, **kwargs: False)
    return clock


@pytest.fixture
def flask_client():
    """The current app's tests' client (tests/http_client.py): Flask's test response names."""
    from app.clients import caching
    from tests import http_client

    caching.cache.clear()
    with http_client.FlaskStyleClient() as c:
        yield c


@pytest.fixture(autouse=True)
def _answers_match_the_api_spec(monkeypatch):
    """Every answer checked against the model the spec shows for it (#449)."""
    from app.v1 import deps
    from tests import answers

    answers.problems.clear()
    monkeypatch.setattr(deps, "ANSWER_CHECK", answers.check)
    yield
    if answers.problems:
        pytest.fail("Answers that don't match the API spec:\n" + "\n".join(answers.problems))


@pytest.fixture
def asgi_client():
    """The whole app as production serves it (the current app's fixture)."""
    from app.api import app as whole_app
    from app.clients import caching

    caching.cache.clear()
    with TestClient(whole_app, base_url="http://localhost") as c:
        yield c


@pytest.fixture
def frontend_build(tmp_path, monkeypatch):
    """A stand-in for web/'s build (``bun run generate``): its page and one asset, so the
    routes serving it are tested without Node (the current app's fixture, #604)."""
    from app.core.legacy_settings import settings

    build = tmp_path / "public"
    (build / "_nuxt").mkdir(parents=True)
    (build / "200.html").write_text(
        '<!DOCTYPE html><html><head><script type="module" src="/_nuxt/entry.abc123.js">'
        '</script></head><body><div id="__nuxt"></div></body></html>'
    )
    (build / "_nuxt" / "entry.abc123.js").write_text("console.log('nuxt');\n" * 200)
    monkeypatch.setattr(settings, "WEB_BUILD_DIR", str(build))
    return build
