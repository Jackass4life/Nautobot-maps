"""conftest.py – shared pytest configuration.

Adds the `demo/` directory to sys.path so that `mock_nautobot` can be imported
by the integration tests without any sys.path manipulation inside the test
modules themselves.

Also provides ``pg_database``: tests that need the persistence database get
their own empty PostgreSQL schema, created before and dropped after the test.
Set ``TEST_DATABASE_URL`` to a database the tests may create schemas in (the
dev container and CI do).  Without it those tests are skipped, except in CI
(``CI`` set), where they fail so they can never be skipped silently.
"""

import os
import sys
import uuid
from urllib.parse import quote

import psycopg
import pytest
from psycopg.rows import dict_row

# Make demo/ importable
_demo_dir = os.path.join(os.path.dirname(__file__), "..", "demo")
if _demo_dir not in sys.path:
    sys.path.insert(0, os.path.abspath(_demo_dir))

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()


@pytest.fixture(autouse=True)
def _settings_live_only_in_settings_module():
    """Fail a test that sets a moved setting on `app` (#165).

    Settings live in nautobot_maps.settings; `app.NAUTOBOT_URL = ...` would
    silently create an unused attribute and the test would test nothing.
    """
    yield
    app_module = sys.modules.get("app")
    if app_module is None:
        return
    from nautobot_maps import settings

    stray = [name for name in settings.SETTING_NAMES if name in vars(app_module)]
    for name in stray:
        delattr(app_module, name)  # so only the offending test fails
    assert not stray, f"set these on nautobot_maps.settings, not on app: {stray}"


def _with_search_path(url: str, schema: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options={quote(f'-csearch_path={schema}')}"


class TestDatabase:
    """Direct access to one test's schema, for seeding and checking rows."""

    __test__ = False  # not a test class

    def __init__(self, url: str):
        self.url = url

    def execute(self, sql: str, params=()) -> list[dict]:
        """Run one statement; return its rows as dicts (``[]`` when it returns none)."""
        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            cur = conn.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def executemany(self, sql: str, rows) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.cursor().executemany(sql, list(rows))


@pytest.fixture
def pg_database(monkeypatch):
    """Point the app at a fresh PostgreSQL schema with the app's tables."""
    if not TEST_DATABASE_URL:
        if os.getenv("CI"):
            pytest.fail("TEST_DATABASE_URL must be set in CI: PostgreSQL tests may not be skipped")
        pytest.skip("TEST_DATABASE_URL not set – skipping PostgreSQL tests")
    import app as flask_app
    from nautobot_maps import settings

    schema = f"test_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    url = _with_search_path(TEST_DATABASE_URL, schema)
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", url)
    flask_app._init_db()
    flask_app.cache.clear()
    try:
        yield TestDatabase(url)
    finally:
        flask_app.cache.clear()
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
