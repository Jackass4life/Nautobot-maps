"""The inventory sync from Nautobot and LibreNMS into the database, and the
background scheduler that runs it (#786, from test_app.py)."""

import asyncio
import threading
from unittest.mock import patch

import pytest

from app.clients import caching, nautobot
from app.repositories import board_repository as board_store
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import scheduler
from app.utils import timeutil
from tests import db as adb
from tests.overrides import overridden


class TestInventoryCacheSync:
    @pytest.fixture(autouse=True)
    def _database(self, override_settings):
        override_settings(
            NAUTOBOT_URL="", NAUTOBOT_TOKEN="", LIBRENMS_URL="", LIBRENMS_API_TOKEN=""
        )

    def test_get_locations_prefers_cached_inventory_without_live_api(self):
        adb.call(
            inventory.write_locations,
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

        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            locations = asyncio.run(inventory.get_locations())

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
        adb.call(
            inventory.write_locations,
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

        locations = asyncio.run(inventory.read_locations(include_without_coordinates=True))
        location = next(item for item in locations if item["id"] == "loc-1")

        assert location["time_zone"] is None

    def test_write_cached_locations_coalesces_explicit_none_text_fields(self):
        adb.call(
            inventory.write_locations,
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

        locations = asyncio.run(inventory.read_locations(include_without_coordinates=True))
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
        adb.call(
            inventory.write_devices,
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

        devices = asyncio.run(inventory.read_devices())
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

    def test_alert_board_uses_cached_inventory_snapshot(self):
        adb.call(
            inventory.write_locations,
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
        adb.call(
            inventory.write_devices,
            [
                {
                    "id": "dev-1",
                    "location_id": "loc-1",
                    "name": "router01",
                    "device_type": "ASR1001-X",
                    "manufacturer": "Cisco",
                    "role": "Core Router",
                    "status": "offline",
                    "primary_ip": "192.0.2.1/32",
                    "platform": "IOS-XE",
                    "serial": "SN123",
                    "tenant": "",
                    "last_updated": "2026-01-01T00:00:00Z",
                }
            ],
        )

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            data = asyncio.run(alerts.get_alert_board_data(force_refresh=True))

        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["id"] == "loc-1"
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][0]["alert_level"] == "critical"

    def test_cached_location_read_triggers_background_refresh_when_sync_is_due(self):
        adb.call(
            inventory.write_locations,
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
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:00:00Z",
            last_successful_sync="2026-01-01T00:00:00Z",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
            error_message="",
        )

        refresh_called = threading.Event()

        def fake_sync(force=False):
            refresh_called.set()

        with (
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            overridden(INVENTORY_SYNC_INTERVAL_SECONDS=0),
            patch.object(inventory, "sync_nautobot", side_effect=fake_sync),
        ):
            locations = asyncio.run(inventory.get_locations())
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
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            asyncio.run(inventory.sync_nautobot(force=True))
            first_state = asyncio.run(inventory.sync_state("nautobot_inventory"))
            asyncio.run(inventory.sync_nautobot())

        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls[0] == {}
        assert device_calls[0] == {"depth": 1}
        assert location_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["depth"] == 1

    def test_incremental_sync_keeps_existing_watermark_without_newer_last_updated(self):
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:05:00Z",
            last_successful_sync="2026-01-01T00:05:00Z",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
            error_message="",
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory_reconcile",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2099-01-01T00:00:00Z",
            last_successful_sync="2099-01-01T00:00:00Z",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
            error_message="",
        )

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
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            asyncio.run(inventory.sync_nautobot())

        state = asyncio.run(inventory.sync_state("nautobot_inventory"))
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

        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:05:00Z",
            last_successful_sync="2026-01-01T00:05:00Z",
            cache_version="1",
            status="idle",
            error_message="",
        )

        with (
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
            patch.object(inventory, "sync_due", return_value=False),
            patch.object(
                timeutil,
                "iso_utc_now",
                side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"],
            ),
        ):
            asyncio.run(inventory.sync_nautobot())

        state = asyncio.run(inventory.sync_state("nautobot_inventory"))
        reconcile_state = asyncio.run(inventory.sync_state("nautobot_inventory_reconcile"))
        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls == [{}]
        assert device_calls == [{"depth": 1}]
        assert state["cache_version"] == inventory.CACHE_VERSION
        assert reconcile_state["cache_version"] == inventory.CACHE_VERSION
        assert state["last_successful_sync"] == "2026-01-02T00:00:00Z"
        assert reconcile_state["last_successful_sync"] == "2026-01-02T00:00:00Z"

    def test_ensure_inventory_snapshot_runs_nautobot_sync_on_cache_version_mismatch(self):
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:05:00Z",
            last_successful_sync="2026-01-01T00:05:00Z",
            cache_version="1",
            status="idle",
            error_message="",
        )

        with (
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            patch.object(inventory, "sync_due", return_value=False),
            patch.object(inventory, "sync_nautobot") as sync_nautobot,
        ):
            assert asyncio.run(inventory.ensure_snapshot(wait=True)) is True

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

        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:05:00Z",
            last_successful_sync="2026-01-01T00:05:00Z",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
            error_message="",
        )

        with (
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
            patch.object(inventory, "sync_due", return_value=True),
            patch.object(
                timeutil,
                "iso_utc_now",
                side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"],
            ),
        ):
            asyncio.run(inventory.sync_nautobot())

        state = asyncio.run(inventory.sync_state("nautobot_inventory"))
        reconcile_state = asyncio.run(inventory.sync_state("nautobot_inventory_reconcile"))
        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls == [{}]
        assert device_calls == [{"depth": 1}]
        assert state["last_successful_sync"] == "2026-01-02T00:00:00Z"
        assert reconcile_state["last_successful_sync"] == "2026-01-02T00:00:00Z"

    def test_primary_ip_backfill_pending_until_current_cache_version_sync(self):
        with patch.object(
            inventory,
            "sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": "1",
            },
        ):
            assert asyncio.run(board_store.nautobot_inventory_primary_ip_backfill_pending()) is True

        with patch.object(
            inventory,
            "sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": inventory.CACHE_VERSION,
            },
        ):
            assert (
                asyncio.run(board_store.nautobot_inventory_primary_ip_backfill_pending()) is False
            )

    def test_alert_board_filters_cached_devices_without_primary_ip_while_backfill_is_pending(self):
        adb.call(
            inventory.write_locations,
            [
                {
                    "id": "loc-1",
                    "name": "Pending Site",
                    "slug": "pending-site",
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
        adb.call(
            inventory.write_devices,
            [
                {
                    "id": "dev-1",
                    "location_id": "loc-1",
                    "name": "router01",
                    "device_type": "ASR1001-X",
                    "manufacturer": "Cisco",
                    "role": "Core Router",
                    "status": "offline",
                    "primary_ip": "",
                    "platform": "IOS-XE",
                    "serial": "SN123",
                    "tenant": "",
                    "last_updated": "2026-01-01T00:00:00Z",
                }
            ],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at=None,
            last_completed_at=None,
            last_successful_sync=None,
            status="pending",
            error_message="",
        )

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            data = asyncio.run(alerts.get_alert_board_data(force_refresh=True))

        assert data["summary"]["no_data"] == 1  # no monitored devices yet
        assert data["alerts"][0]["device_count"] == 0
        assert data["alerts"][0]["down_device_count"] == 0

    def test_api_alerts_filters_cached_devices_without_primary_ip(self, client):
        adb.call(
            inventory.write_locations,
            [
                {
                    "id": "loc-1",
                    "name": "Filtered Site",
                    "slug": "filtered-site",
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
        adb.call(
            inventory.write_devices,
            [
                {
                    "id": "dev-1",
                    "location_id": "loc-1",
                    "name": "ap01",
                    "device_type": "AP",
                    "manufacturer": "Cisco",
                    "role": "Access Point",
                    "status": "offline",
                    "primary_ip": "",
                    "platform": "",
                    "serial": "",
                    "tenant": "",
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "dev-2",
                    "location_id": "loc-1",
                    "name": "router01",
                    "device_type": "ASR1001-X",
                    "manufacturer": "Cisco",
                    "role": "Core Router",
                    "status": "offline",
                    "primary_ip": "192.0.2.1/32",
                    "platform": "IOS-XE",
                    "serial": "SN123",
                    "tenant": "",
                    "last_updated": "2026-01-01T00:00:00Z",
                },
            ],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:05:00Z",
            last_successful_sync="2026-01-01T00:05:00Z",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
            error_message="",
        )

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            resp = client.get("/api/v1/alerts")

        assert resp.status_code == 200
        entry = resp.json()["alerts"][0]
        assert entry["device_count"] == 1
        assert entry["down_device_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-2",
                "device_name": "router01",
                "device_ip": "192.0.2.1",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": [],
                "down_started_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_full_reconcile_prunes_deleted_cached_inventory(self):
        adb.call(
            inventory.write_locations,
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
        adb.call(
            inventory.write_devices,
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
            overridden(NAUTOBOT_URL="https://nautobot.example.com"),
            overridden(NAUTOBOT_TOKEN="token"),
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            asyncio.run(inventory.sync_nautobot(force=True))

        assert [
            item["id"]
            for item in asyncio.run(inventory.read_locations(include_without_coordinates=True))
        ] == ["loc-1"]
        assert [item["id"] for item in asyncio.run(inventory.read_devices())] == ["dev-1"]


# ---------------------------------------------------------------------------
# Tests: background scheduler records history with nobody viewing (#154)
# ---------------------------------------------------------------------------
class TestBackgroundScheduler:
    @pytest.fixture(autouse=True)
    def _database(self, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )
        adb.sql(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        adb.sql(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES ('dev-1', 'loc-1', 'router01', 'router', 'Offline', '10.0.0.1/32')"
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-09-25T11:00:00+00:00",
            last_completed_at="2026-09-25T11:00:00+00:00",
            last_successful_sync="2026-09-25T11:00:00+00:00",
            cache_version=inventory.CACHE_VERSION,
            status="idle",
        )

    def test_tick_after_a_sync_records_alert_history_without_any_request(self):
        with patch.object(inventory, "ensure_snapshot", return_value=True) as ensure:
            assert asyncio.run(scheduler.tick()) is True
        ensure.assert_called_once_with(wait=True)
        rows = adb.sql("SELECT site_id, device_id, status FROM alert_instances")
        assert rows == [{"site_id": "loc-1", "device_id": "dev-1", "status": "open"}]
        # The rebuilt board is cached, so the next page load is instant.
        assert caching.cache.get("alert-board-data:v3")["summary"]["critical"] == 1

    def test_tick_without_a_due_sync_does_nothing(self):
        with (
            patch.object(inventory, "ensure_snapshot", return_value=False),
            patch.object(alerts, "build_alert_board_payload") as build,
        ):
            assert asyncio.run(scheduler.tick()) is False
        build.assert_not_called()

    def test_only_one_process_ticks_at_a_time(self):
        """Another worker holding the lock makes this tick skip."""
        release = adb.hold_lock("background_scheduler")
        assert callable(release)
        try:
            other = adb.hold_lock("background_scheduler")
            assert other is False  # a second connection cannot take it
            with patch.object(inventory, "ensure_snapshot") as ensure:
                assert asyncio.run(scheduler.tick()) is False
            ensure.assert_not_called()
        finally:
            release()

    def test_loop_survives_a_failing_tick(self, monkeypatch):
        calls = []
        monkeypatch.setattr(scheduler, "tick_seconds", lambda: 0)

        async def run():
            stop, running = asyncio.Event(), asyncio.get_running_loop()

            def failing_tick():
                calls.append(1)
                if len(calls) == 2:
                    running.call_soon_threadsafe(stop.set)
                raise RuntimeError("nautobot down")

            monkeypatch.setattr(scheduler, "tick", adb.async_of(failing_tick))
            await asyncio.wait_for(scheduler.loop(stop), 5)

        asyncio.run(run())
        assert len(calls) == 2

    def test_start_is_idempotent_and_respects_the_setting(self, monkeypatch, override_settings):
        monkeypatch.setattr(scheduler, "_started", False)

        async def idle(stop):
            await stop.wait()

        monkeypatch.setattr(scheduler, "loop", adb.async_of(idle))

        async def run():
            override_settings(BACKGROUND_SYNC_ENABLED=False)
            assert scheduler.start() is False
            override_settings(BACKGROUND_SYNC_ENABLED=True)
            assert scheduler.start() is True
            first = scheduler._task
            assert scheduler.start() is True
            assert scheduler._task is first
            scheduler.stop()

        asyncio.run(run())

    def test_tick_seconds_follow_the_sooner_interval(self, override_settings):
        override_settings(INVENTORY_SYNC_INTERVAL_SECONDS=300)
        assert scheduler.tick_seconds() == 30
        override_settings(INVENTORY_SYNC_INTERVAL_SECONDS=10)
        assert scheduler.tick_seconds() == 10
