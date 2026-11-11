"""The inventory sync and its snapshot reads (#600), ported from the current app's
tests/test_app.py (TestPrimaryIpExtraction, TestInventoryCacheSync,
TestLibreNMSEnrichment, TestLibreNMSPolledIp, TestRefreshIsIncremental, the tenant
relationship and description tests, TestLibreNMSMappingsAreBatched).

Tests that go through the alert board or the HTTP routes come with those (steps 6
and 7 of #587).
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.clients import caching, librenms, nautobot
from app.clients import http as upstream_http
from app.core.legacy_settings import settings
from app.db import connection as db
from app.services import contract_service as contract
from app.services import inventory_service as inventory
from app.utils import timeutil
from tests import upstream

OFF = {"disabled": False, "disable_notify": False, "ignore": False}


LOCATION_TENANT_RELATIONSHIPS = [
    {
        "id": "rel-1",
        "key": "site_customers",
        "label": "Site customers",
        "source_type": "dcim.location",
        "destination_type": "tenancy.tenant",
    },
    # The other direction counts too.
    {
        "id": "rel-2",
        "key": "hosted_at",
        "label": "Hosted at",
        "source_type": "tenancy.tenant",
        "destination_type": "dcim.location",
    },
    {
        "id": "rel-3",
        "key": "backup",
        "label": "Backup",
        "source_type": "dcim.device",
        "destination_type": "dcim.device",
    },
]
RELATIONSHIP_ASSOCIATIONS = {
    "rel-1": [
        {"source_id": "loc-1", "destination_id": "ten-2"},
        {"source_id": "loc-1", "destination_id": "ten-3"},
    ],
    "rel-2": [{"source_id": "ten-3", "destination_id": "loc-2"}],
}
TENANT_NAMES = {"ten-1": "Acme Corp", "ten-2": "Nordic Net", "ten-3": "EuroIX"}
# Nautobot tenant descriptions (#263); EuroIX has none.
TENANT_DESCRIPTIONS = {"ten-1": "Bank, 24/7 SLA", "ten-2": "  Shared rack  "}


def _relationship_fetch(error_on=None):
    calls = []

    def fetch(endpoint, params=None, **kwargs):
        calls.append((endpoint, dict(params or {}), kwargs.get("use_cache", True)))
        if error_on and endpoint.startswith(error_on):
            raise upstream.status_error(403)
        if endpoint == "extras/relationships/":
            return LOCATION_TENANT_RELATIONSHIPS
        if endpoint == "extras/relationship-associations/":
            return RELATIONSHIP_ASSOCIATIONS.get(params["relationship"], [])
        if endpoint == "tenancy/tenants/":
            return [
                {"id": tid, "name": name, "description": TENANT_DESCRIPTIONS.get(tid, "")}
                for tid, name in TENANT_NAMES.items()
            ]
        return []

    return fetch, calls


@pytest.fixture(autouse=True)
def empty_cache():
    caching.cache.clear()
    yield
    caching.cache.clear()


class TestPrimaryIpExtraction:
    def test_prefers_primary_ip4_host_before_address(self):
        device = {
            "primary_ip": None,
            "primary_ip4": {"host": "10.11.12.13", "address": "10.11.12.13/25"},
        }

        assert inventory.extract_primary_ip(device) == "10.11.12.13"

    def test_falls_back_to_primary_ip6_then_legacy_primary_ip(self):
        for device, expected in (
            (
                {
                    "primary_ip": "192.0.2.9/32",
                    "primary_ip4": {"host": "10.11.12.13"},
                    "primary_ip6": {"address": "2001:db8::1/64"},
                },
                "10.11.12.13",
            ),
            (
                {
                    "primary_ip": "192.0.2.9/32",
                    "primary_ip4": None,
                    "primary_ip6": {"address": "2001:db8::1/64"},
                },
                "2001:db8::1/64",
            ),
            (
                {"primary_ip": "192.0.2.9/32", "primary_ip4": None, "primary_ip6": None},
                "192.0.2.9/32",
            ),
        ):
            assert inventory.extract_primary_ip(device) == expected


# ---------------------------------------------------------------------------
# Tests: /api/locations
# ---------------------------------------------------------------------------


class TestInventoryCacheSync:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

    def test_get_locations_prefers_cached_inventory_without_live_api(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": ["cached"],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            locations = inventory.get_locations()

        assert locations == [
            {
                "id": "loc-1",
                "name": "Cached Site",
                "slug": "cached-site",
                "status": "Active",
                "location_type": "Data Center",
                "parent": "",
                "parent_id": "",
                "ancestor_path": "",
                "latitude": 1.0,
                "longitude": 2.0,
                "description": "",
                "physical_address": "",
                "country": "",
                "tier": "",
                "tenant_scopes": {},
                "facility": "",
                "tenant": "",
                "tenant_id": "",
                "tenant_group": "",
                "asn": None,
                "time_zone": "",
                "tags": ["cached"],
                "url": "",
                "tenants": [],
                "tenant_descriptions": {},
            }
        ]

    def test_cached_locations_allow_null_time_zone(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": None,
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        locations = inventory.read_locations(include_without_coordinates=True)
        location = next(item for item in locations if item["id"] == "loc-1")

        assert location["time_zone"] is None

    def test_write_cached_locations_coalesces_explicit_none_text_fields(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": None,
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": None,
                            "physical_address": None,
                            "facility": None,
                            "tenant": None,
                            "tenant_id": None,
                            "tenant_group": None,
                            "asn": None,
                            "time_zone": None,
                            "tags": [],
                            "url": None,
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        locations = inventory.read_locations(include_without_coordinates=True)
        assert len(locations) == 1
        assert locations[0]["parent"] == ""
        assert locations[0]["description"] == ""
        assert locations[0]["physical_address"] == ""
        assert locations[0]["facility"] == ""
        assert locations[0]["tenant"] == ""
        assert locations[0]["tenant_id"] == ""
        assert locations[0]["tenant_group"] == ""
        assert locations[0]["time_zone"] is None
        assert locations[0]["url"] == ""

    def test_write_cached_devices_coalesces_explicit_none_text_fields(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-1",
                            "location_id": None,
                            "name": None,
                            "device_type": None,
                            "manufacturer": None,
                            "role": None,
                            "status": None,
                            "primary_ip": None,
                            "platform": None,
                            "serial": None,
                            "tenant": None,
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        devices = inventory.read_devices()
        assert len(devices) == 1
        assert devices[0]["location_id"] == ""
        assert devices[0]["name"] == ""
        assert devices[0]["device_type"] == ""
        assert devices[0]["manufacturer"] == ""
        assert devices[0]["role"] == ""
        assert devices[0]["status"] == ""
        assert devices[0]["primary_ip"] == ""
        assert devices[0]["platform"] == ""
        assert devices[0]["serial"] == ""
        assert devices[0]["tenant"] == ""

    def test_cached_location_read_triggers_background_refresh_when_sync_is_due(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-1",
                            "name": "Cached Site",
                            "slug": "cached-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2026-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:00:00Z",
                    last_successful_sync="2026-01-01T00:00:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        refresh_called = threading.Event()

        def fake_sync(force=False):
            refresh_called.set()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 0),
            patch.object(inventory, "sync_nautobot", side_effect=fake_sync),
        ):
            locations = inventory.get_locations()
            assert refresh_called.wait(1), "expected cached read to trigger a background refresh"

        assert locations[0]["id"] == "loc-1"

    def test_sync_nautobot_inventory_uses_last_successful_sync_watermark(self):
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.sync_nautobot(force=True)
            first_state = inventory.get_sync_state("nautobot_inventory")
            inventory.sync_nautobot()

        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls[0] == {}
        assert device_calls[0] == {"depth": 1}
        assert location_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["depth"] == 1

    def test_incremental_sync_keeps_existing_watermark_without_newer_last_updated(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory_reconcile",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2099-01-01T00:00:00Z",
                    last_successful_sync="2099-01-01T00:00:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        def fake_fetch(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.sync_nautobot()

        state = inventory.get_sync_state("nautobot_inventory")
        assert state["last_successful_sync"] == "2026-01-01T00:05:00Z"

    def test_sync_nautobot_inventory_full_reconciles_when_cache_version_changes(self):
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version="1",
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
            patch.object(inventory, "sync_due", return_value=False),
            patch.object(
                timeutil,
                "iso_utc_now",
                side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"],
            ),
        ):
            inventory.sync_nautobot()

        state = inventory.get_sync_state("nautobot_inventory")
        reconcile_state = inventory.get_sync_state("nautobot_inventory_reconcile")
        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls == [{}]
        assert device_calls == [{"depth": 1}]
        assert state["cache_version"] == inventory.CACHE_VERSION
        assert reconcile_state["cache_version"] == inventory.CACHE_VERSION
        assert state["last_successful_sync"] == "2026-01-02T00:00:00Z"
        assert reconcile_state["last_successful_sync"] == "2026-01-02T00:00:00Z"

    def test_ensure_inventory_snapshot_runs_nautobot_sync_on_cache_version_mismatch(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version="1",
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(inventory, "sync_due", return_value=False),
            patch.object(inventory, "sync_nautobot") as sync_nautobot,
        ):
            assert inventory.ensure_snapshot(wait=True) is True

        sync_nautobot.assert_called_once_with(force=False)

    def test_sync_nautobot_inventory_full_reconcile_advances_watermark(self):
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Site One",
                        "slug": "site-one",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "1.0",
                        "longitude": "2.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    cache_version=inventory.CACHE_VERSION,
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
            patch.object(inventory, "sync_due", return_value=True),
            patch.object(
                timeutil,
                "iso_utc_now",
                side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"],
            ),
        ):
            inventory.sync_nautobot()

        state = inventory.get_sync_state("nautobot_inventory")
        reconcile_state = inventory.get_sync_state("nautobot_inventory_reconcile")
        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls == [{}]
        assert device_calls == [{"depth": 1}]
        assert state["last_successful_sync"] == "2026-01-02T00:00:00Z"
        assert reconcile_state["last_successful_sync"] == "2026-01-02T00:00:00Z"

    def test_sync_librenms_inventory_keeps_postgres_connection_open_between_transactions(self):
        class _FakeResult:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchone(self):
                return self._rows[0] if self._rows else None

        class _FakeTransaction:
            def __init__(self, conn):
                self.conn = conn
                self.nested = False

            def __enter__(self):
                self.conn.transaction_entries += 1
                self.nested = self.conn.transaction_open
                self.conn.transaction_open = True
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                if exc_type is None and not self.nested:
                    self.conn.commits += 1
                self.conn.transaction_open = self.nested
                return False

        class _FakeConn:
            def __init__(self, autocommit=True):
                self.autocommit = autocommit
                self.closed = False
                self.connection_context_entries = 0
                self.transaction_entries = 0
                self.transaction_open = False
                self.commits = 0
                self.queries = []

            def __enter__(self):
                self.connection_context_entries += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                self.closed = True
                return False

            def transaction(self):
                return _FakeTransaction(self)

            def execute(self, query, params=()):
                if self.closed:
                    raise RuntimeError("the connection is closed")
                if not self.autocommit and query.lstrip().upper().startswith("SELECT"):
                    self.transaction_open = True
                self.queries.append((query, params))
                if "SELECT COUNT(*) AS device_count FROM librenms_device_status" in query:
                    return _FakeResult([{"device_count": 1}])
                if (
                    "librenms_observation_seq" in query
                    and "nextval" in query
                    and "INSERT" not in query
                ):
                    return _FakeResult([{"n": 1}])
                return _FakeResult()

            def close(self):
                self.closed = True

        fake_conn = _FakeConn(autocommit=True)
        with (
            patch.object(db, "get_conn", return_value=fake_conn),
            patch.object(settings, "LIBRENMS_URL", "https://librenms.example.com"),
            patch.object(settings, "LIBRENMS_API_TOKEN", "token"),
            patch.object(inventory, "get_sync_state", return_value={"last_successful_sync": None}),
            patch.object(
                librenms,
                "fetch_inventory",
                return_value=[
                    {"device_id": 1, "hostname": "router01", "status": 1, "status_reason": ""}
                ],
            ),
            patch.object(caching.cache, "delete") as cache_delete,
            # Records on its own connection; tested in test_contract.py.
            patch.object(contract, "check_and_record"),
        ):
            inventory.sync_librenms()

        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 2
        assert fake_conn.commits == 2
        assert any(
            "SELECT COUNT(*) AS device_count FROM librenms_device_status" in query
            for query, _ in fake_conn.queries
        )
        assert any("DELETE FROM librenms_device_status" in query for query, _ in fake_conn.queries)
        cache_delete.assert_any_call("alert-board-data:v3")
        cache_delete.assert_any_call("alert-board-data:v3:include-non-operational")
        cache_delete.assert_any_call("location-alert-levels:v1")
        assert cache_delete.call_count == 3

    def test_full_reconcile_prunes_deleted_cached_inventory(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        {
                            "id": "loc-stale",
                            "name": "Stale Site",
                            "slug": "stale-site",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "",
                            "latitude": 1.0,
                            "longitude": 2.0,
                            "description": "",
                            "physical_address": "",
                            "facility": "",
                            "tenant": "",
                            "tenant_id": "",
                            "tenant_group": "",
                            "asn": None,
                            "time_zone": "",
                            "tags": [],
                            "url": "",
                            "last_updated": "2025-01-01T00:00:00Z",
                        }
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        {
                            "id": "dev-stale",
                            "location_id": "loc-stale",
                            "name": "stale-router",
                            "device_type": "ASR1001-X",
                            "manufacturer": "Cisco",
                            "role": "Core Router",
                            "status": "active",
                            "primary_ip": "198.51.100.1/32",
                            "platform": "IOS-XE",
                            "serial": "SN-STALE",
                            "tenant": "",
                            "last_updated": "2025-01-01T00:00:00Z",
                        }
                    ],
                )
        finally:
            conn.close()

        def fake_fetch(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "Fresh Site",
                        "slug": "fresh-site",
                        "status": {"label": "Active"},
                        "location_type": {"name": "Data Center"},
                        "parent": None,
                        "latitude": "3.0",
                        "longitude": "4.0",
                        "description": "",
                        "physical_address": "",
                        "facility": "",
                        "tenant": None,
                        "asn": None,
                        "time_zone": "",
                        "tags": [],
                        "url": "",
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "router01",
                        "location": {"id": "loc-1"},
                        "device_type": {"model": "ASR1001-X", "manufacturer": {"name": "Cisco"}},
                        "role": {"name": "Core Router"},
                        "status": {"label": "Active"},
                        "platform": {"name": "IOS-XE"},
                        "serial": "SN123",
                        "tenant": None,
                        "last_updated": "2026-01-01T00:00:00Z",
                    }
                ]
            return []

        with (
            patch.object(settings, "NAUTOBOT_URL", "https://nautobot.example.com"),
            patch.object(settings, "NAUTOBOT_TOKEN", "token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.sync_nautobot(force=True)

        assert [
            item["id"] for item in inventory.read_locations(include_without_coordinates=True)
        ] == ["loc-1"]
        assert [item["id"] for item in inventory.read_devices()] == ["dev-1"]


# ---------------------------------------------------------------------------
# Tests: criticality_rules.json loading
# ---------------------------------------------------------------------------


class TestLibreNMSEnrichment:
    def setup_method(self):
        self._orig_url = settings.LIBRENMS_URL
        self._orig_token = settings.LIBRENMS_API_TOKEN
        self._orig_verify = settings.LIBRENMS_VERIFY_SSL

    def teardown_method(self):
        settings.LIBRENMS_URL = self._orig_url
        settings.LIBRENMS_API_TOKEN = self._orig_token
        settings.LIBRENMS_VERIFY_SSL = self._orig_verify

    def test_fetch_inventory_skips_when_config_is_blank(self):
        """_fetch_librenms_inventory skips API calls when URL/token are blank."""
        settings.LIBRENMS_URL = "   "
        settings.LIBRENMS_API_TOKEN = "   "
        with patch.object(upstream_http, "request") as mock_get:
            result = librenms.fetch_inventory()
        assert result == []
        mock_get.assert_not_called()

    def test_librenms_get_uses_verify_setting(self):
        settings.LIBRENMS_URL = "https://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        settings.LIBRENMS_VERIFY_SSL = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with patch.object(upstream_http, "request", return_value=mock_resp) as mock_get:
            librenms.get("devices", {"type": "all"})
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False


class TestLibreNMSPolledIp:
    # The current app's migration tests for this column are its own: the schema here is
    # Alembic's, checked against the current one in tests/test_schema.py.

    @pytest.mark.parametrize(
        "record, expected",
        [
            ({"overwrite_ip": "198.51.100.9", "ip": "192.0.2.7"}, "198.51.100.9"),
            ({"overwrite_ip": None, "ip": "192.0.2.7"}, "192.0.2.7"),
            ({"overwrite_ip": "", "ip": "2001:db8::7"}, "2001:db8::7"),
            ({"overwrite_ip": "not-an-ip", "ip": "router01.example.net"}, ""),
            ({}, ""),
        ],
        ids=["override-wins", "ip", "ipv6", "non-ip-ignored", "missing"],
    )
    def test_polled_ip_selection(self, record, expected):
        assert inventory.librenms_polled_ip(record) == expected

    def test_sync_caches_polled_ip(self, monkeypatch, pg_database):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        with patch.object(
            librenms,
            "fetch_inventory",
            return_value=[
                {
                    "device_id": 7,
                    "hostname": "router01.example.net",
                    "ip": "192.0.2.7",
                    "overwrite_ip": None,
                    "status": 1,
                },
            ],
        ):
            inventory.sync_librenms(force=True)
        cached = inventory.read_librenms_devices()
        assert cached == [
            {
                "device_id": 7,
                "hostname": "router01.example.net",
                "ip": "192.0.2.7",
                "status": 1,
                **OFF,
            }
        ]


class TestRefreshIsIncremental:
    @pytest.fixture(autouse=True)
    def _database_and_nautobot(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

    def _location_queries(self, **ensure_kwargs):
        """Run a synchronous sync and return the params sent to dcim/locations/."""
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return []

        with (
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(inventory, "read_location_name_map", return_value={}),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            inventory.ensure_snapshot(wait=True, **ensure_kwargs)
        return [params for endpoint, params in calls if endpoint == "dcim/locations/"]

    def test_sync_now_only_fetches_changes_since_last_sync(self):
        self._location_queries(force=True)  # first sync: full, sets the watermark
        watermark = inventory.get_sync_state("nautobot_inventory")["last_successful_sync"]
        queries = self._location_queries(force=True, full=False)
        assert queries == [{"last_updated__gte": watermark}]

    def test_force_alone_still_means_full_reconcile(self):
        self._location_queries(force=True)
        queries = self._location_queries(force=True)
        assert queries == [{}]


# ---------------------------------------------------------------------------
# Tests: alert board reads in bulk, not per site (#149)
# ---------------------------------------------------------------------------


class TestLocationTenantRelationships:
    """Every tenant of a site, from Nautobot relationships (#238)."""

    def test_both_directions_of_location_tenant_relationships(self, monkeypatch):
        fetch, calls = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        links = inventory.fetch_location_tenant_links()
        assert sorted(
            (link["location_id"], link["tenant"], link["relationship"]) for link in links
        ) == [
            ("loc-1", "EuroIX", "Site customers"),
            ("loc-1", "Nordic Net", "Site customers"),
            ("loc-2", "EuroIX", "Hosted at"),
        ]
        # Only Location <-> Tenant relationships are read, never from the cache.
        read = [
            (endpoint, params.get("relationship"))
            for endpoint, params, _ in calls
            if "associations" in endpoint
        ]
        assert read == [
            ("extras/relationship-associations/", "rel-1"),
            ("extras/relationship-associations/", "rel-2"),
        ]
        assert all(
            use_cache is False for endpoint, _, use_cache in calls if endpoint.startswith("extras/")
        )

    @pytest.mark.parametrize("wanted", ["site_customers", "site customers"])
    def test_setting_narrows_by_key_or_label(self, monkeypatch, wanted):
        fetch, _ = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        monkeypatch.setattr(settings, "SITE_TENANT_RELATIONSHIPS", {wanted})
        links = inventory.fetch_location_tenant_links()
        assert {link["relationship"] for link in links} == {"Site customers"}

    def test_no_relationship_is_an_empty_list(self, monkeypatch):
        fetch, _ = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        monkeypatch.setattr(settings, "SITE_TENANT_RELATIONSHIPS", {"nothing-matches"})
        assert inventory.fetch_location_tenant_links() == []

    def test_a_failed_read_is_none(self, monkeypatch):
        fetch, _ = _relationship_fetch(error_on="extras/relationship-associations/")
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        assert inventory.fetch_location_tenant_links() is None

    def test_site_tenants_puts_its_own_tenant_first(self):
        assert inventory.site_tenants("Acme", ["Beta", "Acme", "Gamma"]) == [
            "Acme",
            "Beta",
            "Gamma",
        ]
        assert inventory.site_tenants("", ["Beta"]) == ["Beta"]
        assert inventory.site_tenants("", []) == []


class TestLocationTenantSync:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

    def _sync(self, monkeypatch, error_on=None):
        fetch, _ = _relationship_fetch(error_on)

        def with_inventory(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "London",
                        "tenant": {"id": "ten-1", "name": "Acme Corp"},
                        "latitude": "51.5",
                        "longitude": "-0.1",
                        "location_type": {"id": "lt", "name": "Data Center"},
                    }
                ]
            if endpoint == "dcim/devices/":
                return [
                    {
                        "id": "dev-1",
                        "name": "lon-core",
                        "location": {"id": "loc-1"},
                        "role": {"name": "Core Router"},
                        "status": {"name": "Offline"},
                        "primary_ip4": {"address": "10.0.0.1/32"},
                    }
                ]
            return fetch(endpoint, params, **kwargs)

        monkeypatch.setattr(nautobot, "fetch_all_pages", with_inventory)
        inventory.sync_nautobot(force=True)
        caching.cache.clear()

    # Its tests read the board and the map: they come with steps 6 and 7 of #587.


class TestTenantDescriptions:
    """The tenant (i) shows the Nautobot description (#263)."""

    @pytest.fixture(autouse=True)
    def _sync_setup(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")

    _sync = TestLocationTenantSync._sync

    def test_fetch_reads_every_tenant_fresh(self, monkeypatch):
        fetch, calls = _relationship_fetch()
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        tenants = {tenant["name"]: tenant["description"] for tenant in inventory.fetch_tenants()}
        assert tenants == {"Acme Corp": "Bank, 24/7 SLA", "Nordic Net": "Shared rack", "EuroIX": ""}
        assert ("tenancy/tenants/", {}, False) in calls

    def test_fetch_failure_is_none(self, monkeypatch):
        fetch, _ = _relationship_fetch(error_on="tenancy/tenants/")
        monkeypatch.setattr(nautobot, "fetch_all_pages", fetch)
        assert inventory.fetch_tenants() is None

    def test_map_without_a_database_reads_them_from_nautobot(self, monkeypatch):
        """Map-only setups (no PostgreSQL) get the (i) too (Copilot review on #264)."""
        fetch, _ = _relationship_fetch()

        def live(endpoint, params=None, **kwargs):
            if endpoint == "dcim/locations/":
                return [
                    {
                        "id": "loc-1",
                        "name": "London",
                        "tenant": {"id": "ten-1"},
                        "latitude": "51.5",
                        "longitude": "-0.1",
                    },
                    {
                        "id": "loc-2",
                        "name": "Paris",
                        "tenant": {"id": "ten-3"},
                        "latitude": "48.8",
                        "longitude": "2.3",
                    },
                ]
            return fetch(endpoint, params, **kwargs)

        monkeypatch.setattr(db, "get_conn", lambda *args, **kwargs: None)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
        monkeypatch.setattr(nautobot, "fetch_all_pages", live)
        by_name = {loc["name"]: loc for loc in inventory.get_locations()}
        assert by_name["London"]["tenant_descriptions"] == {"Acme Corp": "Bank, 24/7 SLA"}
        assert by_name["Paris"]["tenant_descriptions"] == {}  # EuroIX has no description

    def test_a_failed_read_keeps_the_descriptions(self, monkeypatch):
        self._sync(monkeypatch)
        monkeypatch.setattr(inventory, "fetch_tenants", lambda: None)
        self._sync(monkeypatch)
        assert inventory.read_tenant_descriptions() == {
            "Acme Corp": "Bank, 24/7 SLA",
            "Nordic Net": "Shared rack",
        }
