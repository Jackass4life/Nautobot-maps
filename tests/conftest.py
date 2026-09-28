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


# Names moved out of app.py (#165). Assigning one on `app` would silently create
# an unused attribute, so the test would test nothing.
_MOVED_FROM_APP = {
    "db": (
        "_current_persistence_dialect",
        "_db_transaction",
        "_serialize_value",
        "_row_to_dict",
        "_sql_placeholders",
        "_sql_now",
        "_build_alert_key",
        "_advisory_lock_key",
        "_get_db_conn",
        "_acquire_db_inventory_lock",
        "_init_db",
        "_migrate_open_alert_keys",
        "_mark_nautobot_inventory_sync_pending",
        "psycopg",
        "dict_row",
    ),
    "caching": ("_cache_get", "_cache_set", "_invalidate_alert_board_cache"),
    "nautobot": (
        "_configure_nautobot_ssl_warnings",
        "_nested_str",
        "_build_id_name_map",
        "_build_device_type_maps",
        "_build_tenant_group_map",
        "nautobot_get",
        "nautobot_post",
        "nautobot_delete",
        "fetch_all_pages",
        "_build_device_lookup_maps",
    ),
    "librenms": ("_librenms_get", "_fetch_librenms_inventory"),
    "timeutil": (
        "_iso_utc_now",
        "_parse_iso_datetime",
        "_max_last_updated",
        "_next_watermark",
        "_max_iso_datetime_value",
    ),
    "inventory": (
        "_json_load_list",
        "_normalize_locations",
        "_extract_primary_ip",
        "_normalize_librenms_host_key",
        "_normalize_librenms_ip_key",
        "_is_ip_literal",
        "_normalize_devices",
        "_read_cached_location_name_map",
        "_read_cached_locations",
        "_read_cached_devices",
        "_read_cached_librenms_inventory",
        "_record_sync_state",
        "_get_sync_state",
        "_sync_due",
        "_nautobot_inventory_cache_version_mismatch",
        "_nautobot_snapshot_initialized",
        "_coalesce_cache_text",
        "_write_cached_locations",
        "_write_cached_devices",
        "_librenms_polled_ip",
        "_write_cached_librenms_devices",
        "_sync_nautobot_inventory",
        "_sync_librenms_inventory",
        "_ensure_inventory_snapshot",
        "get_locations",
        "_FULL_RECONCILE_INTERVAL_SECONDS",
        "_NAUTOBOT_INVENTORY_CACHE_VERSION",
        "_inventory_sync_lock",
    ),
}


@pytest.fixture(autouse=True)
def _moved_names_are_not_set_on_app():
    """Fail a test that sets a moved setting or function on `app` (#165)."""
    yield
    app_module = sys.modules.get("app")
    if app_module is None:
        return
    from nautobot_maps import settings

    moved = {name: "settings" for name in settings.SETTING_NAMES}
    for module, names in _MOVED_FROM_APP.items():
        moved.update(dict.fromkeys(names, module))
    stray = {name: moved[name] for name in moved if name in vars(app_module)}
    for name in stray:
        delattr(app_module, name)  # so only the offending test fails
    assert not stray, f"set these on their nautobot_maps module, not on app: {stray}"


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
    from nautobot_maps import db, settings

    schema = f"test_{uuid.uuid4().hex[:16]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    url = _with_search_path(TEST_DATABASE_URL, schema)
    monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", url)
    db.init_db()
    flask_app.cache.clear()
    try:
        yield TestDatabase(url)
    finally:
        flask_app.cache.clear()
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
