"""TODO docstring (#786)."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, patch

import pytest

from app.clients import caching, librenms, nautobot
from app.core import settings as config
from app.core.settings import settings
from app.db import aconnection
from app.db import connection as db
from app.repositories import board_repository as board_store
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from app.services import maintenance_service as maintenance
from app.utils import timeutil
from app.v1.alert_board import dtos as board_answers
from tests import answers, upstream
from tests import db as adb
from tests.fakes import _full_locations
from tests.overrides import overridden


# ---------------------------------------------------------------------------
# Tests: alert board
# ---------------------------------------------------------------------------
class TestAlertBoard:
    def test_alert_board_page_renders(self, client, frontend_build):
        """The board and the wall are built with Nuxt (#463; web/tests/ and e2e/
        test the pages themselves)."""
        for path in ("/alerts?tenant=Acme%20Corp", "/alerts?view=wall&q=DNK"):
            resp = client.get(path)
            assert resp.status_code == 200 and b'id="__nuxt"' in resp.content, path
            assert resp.headers["Cache-Control"] == "no-cache"

    def test_get_alert_board_data_aggregates_and_sorts(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Bravo Site",
                "status": "Active",
                "location_type": "Data Center",
                "parent": "Region A",
                "latitude": 10.0,
                "longitude": 20.0,
                "description": "",
                "physical_address": "",
                "facility": "",
                "tenant": "Tenant A",
                "tenant_id": "ten-1",
                "tenant_group": "Group A",
                "asn": None,
                "time_zone": "",
                "tags": [],
                "url": "",
            },
            {
                "id": "loc-2",
                "name": "Alpha Site",
                "status": "Active",
                "location_type": "Office",
                "parent": "",
                "latitude": 11.0,
                "longitude": 21.0,
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
            },
            {
                "id": "loc-3",
                "name": "Charlie Site",
                "status": "Planned",
                "location_type": "PoP",
                "parent": "",
                "latitude": 12.0,
                "longitude": 22.0,
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
            },
        ]

        def mock_devices(location_id, location_type=None, **_kwargs):
            if location_id == "loc-1":
                return (
                    [{"id": "d1", "name": "core1", "role": "Core Router", "status": "offline"}],
                    {"level": "critical", "reason": "Core device(s) offline: core1"},
                )
            if location_id == "loc-2":
                return (
                    [
                        {"id": "d2", "name": "sw1", "role": "Switch", "status": "offline"},
                        {"id": "d3", "name": "sw2", "role": "Switch", "status": "active"},
                        {"id": "d4", "name": "sw3", "role": "Switch", "status": "active"},
                    ],
                    {"level": "medium", "reason": "1/3 devices offline (33%)"},
                )
            return (
                [{"id": "d5", "name": "sw4", "role": "Switch", "status": "active"}],
                {"level": "ok", "reason": ""},
            )

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(alerts, "get_location_devices_and_alert", side_effect=mock_devices),
        ):
            data = asyncio.run(alerts.get_alert_board_data())

        assert data["summary"] == {
            "total": 3,
            "critical": 1,
            "medium": 1,
            "low": 0,
            "no_data": 0,
            "ok": 1,
            "non_ok": 2,
            "maintenance": 0,
            "wireless": 0,
        }
        assert [item["id"] for item in data["alerts"]] == ["loc-1", "loc-2", "loc-3"]
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][1]["alert_level"] == "medium"
        assert data["stale"] is False

    def test_api_alerts_returns_board_data(self, client):
        caching.cache.clear()
        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=_full_locations(
                    [
                        {
                            "id": "loc-1",
                            "name": "Copenhagen DC",
                            "status": "Active",
                            "location_type": "Data Center",
                            "parent": "Denmark",
                            "latitude": 55.6761,
                            "longitude": 12.5683,
                            "description": "",
                            "physical_address": "Vermlandsgade 51, 2300 Copenhagen",
                            "country": "Denmark",
                            "facility": "CPH-1",
                            "tenant": "Acme Corp",
                            "tenant_id": "ten-1",
                            "tenant_group": "Corporate",
                            "asn": 65001,
                            "time_zone": "Europe/Copenhagen",
                            "tags": ["critical"],
                            "url": "",
                        }
                    ]
                ),
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "offline",
                            "primary_ip": "10.0.0.1/32",
                        }
                    ],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
            ),
        ):
            resp = client.get("/api/v1/alerts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["name"] == "Copenhagen DC"
        assert data["alerts"][0]["alert_level"] == "critical"
        assert data["alerts"][0]["physical_address"] == "Vermlandsgade 51, 2300 Copenhagen"
        assert data["alerts"][0]["country"] == "Denmark"
        assert data["alerts"][0]["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "device_ip": "10.0.0.1",
                "status": "offline",
                "role": "Core Router",
                "core": True,  # the likely cause of the site's other down devices (#285)
                "case_numbers": [],
            }
        ]

    def test_api_alerts_hides_non_operational_locations_unless_requested(self, client):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Production Site",
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
            },
            {
                "id": "loc-2",
                "name": "Spare Kit",
                "status": "Active",
                "location_type": "Warehouse",
                "parent": "",
                "latitude": 3.0,
                "longitude": 4.0,
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
            },
        ]

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [
                        {
                            "id": "dev-1",
                            "name": "router01",
                            "role": "Core Router",
                            "status": "active",
                            "primary_ip": "192.0.2.1/32",
                        }
                    ],
                    {"level": "ok", "reason": ""},
                ),
            ) as get_alert,
        ):
            resp = client.get("/api/v1/alerts")
            assert resp.status_code == 200
            assert resp.json()["summary"]["total"] == 1
            assert [item["id"] for item in resp.json()["alerts"]] == ["loc-1"]
            assert get_alert.call_count == 1

            caching.cache.clear()
            get_alert.reset_mock()
            resp = client.get("/api/v1/alerts?include_non_operational=1")

        assert resp.status_code == 200
        assert resp.json()["summary"]["total"] == 2
        assert [item["id"] for item in resp.json()["alerts"]] == ["loc-1", "loc-2"]
        assert get_alert.call_count == 2

    def test_location_exclusion_supports_object_tags(self):
        with overridden(ALERT_BOARD_EXCLUDED_LOCATION_TAGS={"non-operational"}):
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Warehouse",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": [{"name": "Non-Operational"}],
                }
            )

    def test_status_exclusion_null_keyword_matches_missing_status(self):
        excluded = config.parse_csv_set("null, Decommissioning")
        assert alerts.status_is_excluded(None, excluded)
        assert alerts.status_is_excluded("", excluded)
        assert alerts.status_is_excluded("  ", excluded)
        assert alerts.status_is_excluded("DECOMMISSIONING", excluded)
        assert not alerts.status_is_excluded("Active", excluded)
        # Without the keyword an empty status is never excluded.
        assert not alerts.status_is_excluded("", {"decommissioning"})

    def test_location_exclusion_null_status(self):
        with overridden(ALERT_BOARD_EXCLUDED_LOCATION_STATUSES={"null"}):
            assert alerts.location_is_excluded_from_alert_board({"name": "Site", "status": ""})
            assert alerts.location_is_excluded_from_alert_board({"name": "Site"})
            assert not alerts.location_is_excluded_from_alert_board(
                {"name": "Site", "status": "Active"}
            )

    def test_excluded_device_statuses_are_not_scored(self):
        devices = [
            {
                "id": "d1",
                "name": "router01",
                "role": "Router",
                "status": "Decommissioning",
                "primary_ip": "10.0.0.1",
            },
            {"id": "d2", "name": "sw01", "role": "Switch", "status": "", "primary_ip": "10.0.0.2"},
            {
                "id": "d3",
                "name": "sw02",
                "role": "Switch",
                "status": "Active",
                "primary_ip": "10.0.0.3",
            },
        ]
        with patch.object(alerts, "criticality_overrides", return_value={}):
            scored, alert = asyncio.run(
                alerts.get_location_devices_and_alert(
                    "loc-1",
                    devices_data=devices,
                    devices_already_normalized=True,
                    require_primary_ip=True,
                    excluded_device_statuses={"decommissioning", "null"},
                )
            )
            unfiltered, unfiltered_alert = asyncio.run(
                alerts.get_location_devices_and_alert(
                    "loc-1",
                    devices_data=devices,
                    devices_already_normalized=True,
                    require_primary_ip=True,
                )
            )

        assert [device["name"] for device in scored] == ["sw02"]
        assert alert["level"] == "ok"
        # Without the setting, Decommissioning still counts as a down core device.
        assert len(unfiltered) == 3
        assert unfiltered_alert["level"] == "critical"

    def test_parse_csv_set_normalizes_case_and_whitespace(self):
        assert config.parse_csv_set(" Decommissioning ; Core Site, POP ") == {
            "decommissioning",
            "core site",
            "pop",
        }

    def test_location_exclusion_matches_mixed_case_configured_values(self):
        with (
            overridden(ALERT_BOARD_EXCLUDED_LOCATION_NAMES=config.parse_csv_set("Warehouse")),
            overridden(
                ALERT_BOARD_EXCLUDED_LOCATION_STATUSES=config.parse_csv_set("Decommissioning")
            ),
            overridden(ALERT_BOARD_EXCLUDED_LOCATION_TYPES=config.parse_csv_set("Branch Office")),
            overridden(ALERT_BOARD_EXCLUDED_LOCATION_TAGS=config.parse_csv_set("Non-Operational")),
        ):
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "warehouse",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": [],
                }
            )
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Site A",
                    "status": "decommissioning",
                    "location_type": "Office",
                    "tags": [],
                }
            )
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Site B",
                    "status": "Active",
                    "location_type": "branch office",
                    "tags": [],
                }
            )
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Site C",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": ["non-operational"],
                }
            )

    def test_get_alert_board_data_marks_location_no_data_on_error(self):
        caching.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Broken Site", "latitude": None, "longitude": None}
        ]

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
            ),
        ):
            data = asyncio.run(alerts.get_alert_board_data())

        assert data["summary"]["no_data"] == 1
        assert data["summary"]["ok"] == 0
        assert data["alerts"][0]["alert_level"] == "no_data"
        assert data["alerts"][0]["alert_reason"] == "Could not compute alert state"

    def test_location_alert_uses_cached_snapshot_only_on_device_cache_miss(self):
        with (
            patch.object(inventory, "read_devices", return_value=[]),
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
            patch.object(
                nautobot,
                "fetch_all_pages",
                side_effect=AssertionError("should not fetch live inventory"),
            ),
        ):
            devices, alert = asyncio.run(
                alerts.get_location_devices_and_alert(
                    "loc-1",
                    "Data Center",
                    snapshot_only=True,
                )
            )

        assert devices == []
        assert alert == {"level": "no_data", "reason": "No monitored devices"}
        ensure_snapshot.assert_not_called()

    def test_location_alert_filters_devices_without_primary_ip(self):
        devices, alert = asyncio.run(
            alerts.get_location_devices_and_alert(
                "loc-1",
                "Data Center",
                devices_data=[
                    {
                        "id": "dev-1",
                        "name": "ap01",
                        "device_type": "AP",
                        "manufacturer": "Cisco",
                        "role": "Access Point",
                        "status": "offline",
                        "primary_ip": "",
                        "platform": "",
                        "serial": "",
                        "tenant": "",
                    },
                    {
                        "id": "dev-2",
                        "name": "router01",
                        "device_type": "ASR1001-X",
                        "manufacturer": "Cisco",
                        "role": "Core Router",
                        "status": "offline",
                        "primary_ip": "192.0.2.1/32",
                        "platform": "",
                        "serial": "",
                        "tenant": "",
                    },
                ],
                devices_already_normalized=True,
                snapshot_only=True,
                require_primary_ip=True,
            )
        )

        assert [device["id"] for device in devices] == ["dev-2"]
        assert alert == {
            "level": "critical",
            "reason": "Core device(s) offline: router01",
        }

    def test_location_detail_live_device_fetch_retries_with_location_filter_on_400(self):
        bad_request = upstream.status_error(400)
        live_device_page = [
            {
                "id": "dev-1",
                "name": "router01",
                "device_type": {"model": "ASR9006", "manufacturer": {"name": "Cisco"}},
                "role": {"name": "Core Router"},
                "status": {"label": "active"},
                "platform": None,
                "serial": "ABC123",
                "tenant": None,
            }
        ]
        fetch_calls = []

        def _mock_fetch(endpoint, params=None, **kwargs):
            fetch_calls.append((endpoint, params))
            if len(fetch_calls) == 1:
                raise bad_request
            return live_device_page

        with (
            patch.object(inventory, "read_devices", side_effect=[[], []]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(nautobot, "fetch_all_pages", side_effect=_mock_fetch),
        ):
            devices, alert = asyncio.run(
                alerts.get_location_devices_and_alert("loc-1", "Data Center")
            )

        assert len(devices) == 1
        assert alert["level"] == "ok"
        assert fetch_calls[:2] == [
            ("dcim/devices/", {"location_id": "loc-1", "depth": 1}),
            ("dcim/devices/", {"location": "loc-1", "depth": 1}),
        ]

    def test_get_alert_board_data_does_not_live_fetch_devices_on_cache_miss(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Site 1",
                "location_type": "Data Center",
                "latitude": 1.0,
                "longitude": 2.0,
            },
            {
                "id": "loc-2",
                "name": "Site 2",
                "location_type": "Office",
                "latitude": 3.0,
                "longitude": 4.0,
            },
        ]

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(inventory, "read_devices", return_value=[]),
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
            patch.object(
                nautobot,
                "fetch_all_pages",
                side_effect=AssertionError("should not fetch live inventory"),
            ),
        ):
            data = asyncio.run(alerts.get_alert_board_data(force_refresh=True))

        # Sites without cached devices have nothing to judge (#124).
        assert data["summary"]["no_data"] == 2
        assert data["summary"]["non_ok"] == 0
        assert [item["alert_level"] for item in data["alerts"]] == ["no_data", "no_data"]
        ensure_snapshot.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )

    def test_get_alert_board_data_uses_nautobot_alerts_when_librenms_unavailable(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Site 1",
                "location_type": "Data Center",
                "latitude": 1.0,
                "longitude": 2.0,
            },
            {
                "id": "loc-2",
                "name": "Site 2",
                "location_type": "Office",
                "latitude": 3.0,
                "longitude": 4.0,
            },
        ]

        with (
            overridden(LIBRENMS_URL="https://librenms.example.com"),
            overridden(LIBRENMS_API_TOKEN="token"),
            patch.object(librenms, "fetch_inventory", side_effect=RuntimeError("down")),
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                side_effect=[
                    (
                        [{"id": "d1", "status": "offline"}],
                        {"level": "critical", "reason": "Core down"},
                    ),
                    ([{"id": "d2", "status": "active"}], {"level": "ok", "reason": ""}),
                ],
            ) as get_alert,
        ):
            data = asyncio.run(alerts.get_alert_board_data(force_refresh=True))

        assert get_alert.call_count == 2
        assert data["summary"]["critical"] == 1
        assert data["summary"]["ok"] == 1
        assert data["summary"]["no_data"] == 0

    def test_get_alert_board_data_skips_lifecycle_upsert_until_first_successful_nautobot_sync(self):
        caching.cache.clear()
        sample_locations = [
            {
                "id": "loc-1",
                "name": "Site 1",
                "location_type": "Data Center",
                "latitude": 1.0,
                "longitude": 2.0,
            },
        ]

        class _FakeConn:
            async def execute(self, *args, **kwargs):
                raise RuntimeError("no database here")

        async def close():
            return None

        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(aconnection, "open_connection", return_value=(_FakeConn(), close)),
            # Connection handling only: no maintenance windows (#283).
            patch.object(
                maintenance,
                "read_active",
                return_value={"sites": {}, "devices": {}, "next_change": None},
            ),
            patch.object(
                inventory,
                "sync_state",
                return_value={
                    "status": "error",
                    "last_successful_sync": None,
                },
            ),
            patch.object(alerts, "upsert_alert_lifecycle_for_site") as upsert,
            patch.object(
                alerts,
                "read_alert_context",
                return_value={
                    "active_alert_instance_count": 1,
                    "historical_downtime_seconds": 3600,
                    "current_downtime_seconds": 1800,
                    "active_cases": ["INC-1001"],
                    "down_devices": [{"device_id": "dev-legacy", "device_name": "legacy01"}],
                },
            ) as get_context,
        ):
            data = asyncio.run(alerts.get_alert_board_data(force_refresh=True))

        assert data["summary"]["total"] == 1
        upsert.assert_not_called()
        get_context.assert_called_once()
        entry = data["alerts"][0]
        assert entry["active_alert_instance_count"] == 0
        assert entry["current_downtime_seconds"] == 0
        assert entry["historical_downtime_seconds"] == 3600
        assert entry["active_cases"] == []
        assert entry["down_devices"] == []


# ---------------------------------------------------------------------------
# Tests: alert-board tier definitions and per-device IP (#117)
# ---------------------------------------------------------------------------
class TestAlertBoardTierDefinitions:
    def test_the_board_explains_the_levels_and_chips(self, client):
        """The (i) by the summary line explains the levels, and each chip what it shows (#311),
        from the configured thresholds (#293): the board reads them from /api/page-config (#463)."""
        levels = client.get("/api/v1/page-config").json()["board"]["levels"]
        assert levels == alerts.alert_level_definitions()
        assert set(levels) >= {
            "critical",
            "medium",
            "low",
            "alarms",
            "total",
            "no_data",
            "maintenance",
        }


class TestDeviceDisplayIp:
    def test_prefers_primary_ip_without_prefix_length(self):
        device = {"primary_ip": "192.0.2.10/32", "librenms_hostname": "198.51.100.1"}
        assert alerts.device_display_ip(device) == "192.0.2.10"

    def test_keeps_ipv6_primary_ip(self):
        assert alerts.device_display_ip({"primary_ip": "2001:db8::1/128"}) == "2001:db8::1"

    def test_falls_back_to_librenms_ip_hostname(self):
        assert (
            alerts.device_display_ip({"primary_ip": "", "librenms_hostname": "198.51.100.1"})
            == "198.51.100.1"
        )

    def test_ignores_non_ip_librenms_hostname(self):
        assert (
            alerts.device_display_ip(
                {"primary_ip": "", "librenms_hostname": "router01.example.net"}
            )
            == ""
        )

    def test_enrichment_records_matched_librenms_hostname(self):
        orig_url, orig_token = settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN
        settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN = (
            "https://librenms.test",
            "tok",
        )
        try:
            devices = [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}]
            enriched = asyncio.run(
                alerts.enrich_with_librenms(
                    devices,
                    lnms_devices=[{"device_id": 7, "hostname": "router01", "status": 1}],
                    lnms_id_map={},
                )
            )
        finally:
            settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN = orig_url, orig_token
        assert enriched[0]["librenms_hostname"] == "router01"


# ---------------------------------------------------------------------------
# Tests: alert-board refresh, cold start and sync progress (#121)
# ---------------------------------------------------------------------------
class TestAlertBoardSyncProgress:
    @pytest.fixture(autouse=True)
    def _persistence(self):
        caching.cache.clear()
        yield
        caching.cache.clear()

    def _set_nautobot_sync_state(self, status, started_at, completed_at=None):
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at=started_at,
            last_completed_at=completed_at,
            last_successful_sync=completed_at,
            cache_version=inventory.CACHE_VERSION,
            status=status,
            error_message="",
        )

    def _board(self, alerts=None):
        board = {
            "checked_at": timeutil.iso_utc_now(),
            "stale_after_seconds": 300,
            "summary": {"total": len(alerts or [])},
            "alerts": alerts or [],
        }
        # A real board's other fields, empty (#449: the answer is checked against /api/docs).
        return answers.complete(board_answers.Board, board)

    def test_refresh_1_enqueues_forced_background_sync(self, client):
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            resp = client.get("/api/v1/alerts?refresh=1")
        assert resp.status_code == 200
        ensure.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )
        assert resp.json()["sync_pending"] is True

    def test_timestamp_refresh_value_does_not_trigger_sync(self, client):
        """The old UI sent refresh=<Date.now()>; the server contract is 1/true/yes/refresh."""
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            resp = client.get("/api/v1/alerts?refresh=1727000000000")
        ensure.assert_not_called()
        assert resp.json()["sync_pending"] is False

    def test_cold_start_enqueues_first_sync_without_waiting(self, client, override_settings):
        override_settings(NAUTOBOT_URL="https://nautobot.example.com", NAUTOBOT_TOKEN="token")
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board()),
        ):
            resp = client.get("/api/v1/alerts")
        assert resp.status_code == 200
        ensure.assert_called_once_with(wait=False)
        data = resp.json()
        assert data["alerts"] == []
        assert data["sync_pending"] is True
        # The empty cold-start board must not be cached, or the synced data
        # would stay hidden until the cache expires.
        assert caching.cache.get("alert-board-data:v3") is None

    def test_initialized_snapshot_does_not_start_sync_and_is_not_pending(self, client):
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot") as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            resp = client.get("/api/v1/alerts")
        ensure.assert_not_called()
        assert resp.json()["sync_pending"] is False

    def test_due_sync_is_started_by_a_normal_board_load(self, client, override_settings):
        """An open board keeps itself up to date: a normal load starts a sync once one is due (#152)."""
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            INVENTORY_SYNC_INTERVAL_SECONDS=300,
        )
        old = (datetime.now(UTC) - timedelta(seconds=301)).isoformat()
        self._set_nautobot_sync_state("idle", old, old)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            data = client.get("/api/v1/alerts").json()
        ensure.assert_called_once_with(wait=False)
        assert data["sync_pending"] is True
        assert data["next_update_in_seconds"] == 0

    def test_next_update_counts_down_from_last_completed_sync(self, client, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            INVENTORY_SYNC_INTERVAL_SECONDS=300,
        )
        completed = (datetime.now(UTC) - timedelta(seconds=100)).isoformat()
        self._set_nautobot_sync_state("idle", completed, completed)
        with (
            patch.object(inventory, "ensure_snapshot") as ensure,
            patch.object(
                alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])
            ),
        ):
            data = client.get("/api/v1/alerts").json()
        ensure.assert_not_called()
        assert 195 <= data["next_update_in_seconds"] <= 200

    def test_next_update_uses_the_sooner_source(self, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="https://librenms.example.com",
            LIBRENMS_API_TOKEN="token",
            INVENTORY_SYNC_INTERVAL_SECONDS=3600,
            LIBRENMS_SYNC_INTERVAL_SECONDS=120,
        )
        now = datetime.now(UTC).isoformat()
        self._set_nautobot_sync_state("idle", now, now)
        adb.call(
            inventory.record_sync_state,
            "librenms_inventory",
            last_started_at=now,
            last_completed_at=now,
            status="idle",
        )
        due, next_in = asyncio.run(alerts.inventory_update_schedule())
        assert due is False
        assert 115 <= next_in <= 120

    def test_next_update_unknown_while_sync_runs(self, override_settings):
        override_settings(NAUTOBOT_URL="https://nautobot.example.com", NAUTOBOT_TOKEN="token")
        self._set_nautobot_sync_state("running", timeutil.iso_utc_now())
        assert asyncio.run(alerts.inventory_update_schedule()) == (False, None)

    def test_next_update_unknown_without_persistence(self, override_settings):
        override_settings(NAUTOBOT_MAPS_DATABASE_URL="")
        assert asyncio.run(alerts.inventory_update_schedule()) == (False, None)

    def test_running_sync_is_reported_as_pending(self, client):
        self._set_nautobot_sync_state("running", timeutil.iso_utc_now())
        assert asyncio.run(alerts.nautobot_sync_in_progress()) is True

    def test_abandoned_running_sync_is_not_pending(self):
        started = datetime(2020, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        self._set_nautobot_sync_state("running", started)
        assert asyncio.run(alerts.nautobot_sync_in_progress()) is False

    def test_no_sync_pending_without_persistence(self, override_settings):
        override_settings(NAUTOBOT_MAPS_DATABASE_URL="")
        assert asyncio.run(alerts.nautobot_sync_in_progress()) is False

    def test_adding_case_invalidates_cached_board(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        asyncio.run(
            alerts.upsert_alert_lifecycle_for_site(
                site,
                devices_down,
                {"level": "critical", "reason": "Core device(s) offline: router01"},
                timeutil.iso_utc_now(),
            )
        )
        caching.cache.set("alert-board-data:v3", self._board([{"id": "loc-1"}]))
        resp = client.post(
            "/api/v1/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-2001"},
        )
        assert resp.status_code == 200
        assert caching.cache.get("alert-board-data:v3") is None


# ---------------------------------------------------------------------------
class TestAlertBoardWithoutPersistence:
    def test_payload_reports_configured_database(self, client, monkeypatch):
        pass  # the schema: Alembic's (tests/test_schema.py)
        caching.cache.clear()
        with (
            patch.object(inventory, "ensure_snapshot", return_value=False),
            patch.object(
                alerts,
                "build_alert_board_payload",
                return_value=answers.complete(
                    board_answers.Board,
                    {"checked_at": timeutil.iso_utc_now(), "summary": {}, "alerts": []},
                ),
            ),
        ):
            data = client.get("/api/v1/alerts").json()
        assert data["persistence_configured"] is True


# ---------------------------------------------------------------------------
class TestRefreshIsIncremental:
    @pytest.fixture(autouse=True)
    def _database_and_nautobot(self, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )
        pass  # the schema: Alembic's (tests/test_schema.py)

    def _location_queries(self, **ensure_kwargs):
        """Run a synchronous sync and return the params sent to dcim/locations/."""
        calls = []

        def fake_fetch(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return []

        with (
            patch.object(nautobot, "fetch_all_pages", side_effect=fake_fetch),
            patch.object(nautobot, "device_lookup_maps", return_value={}),
        ):
            asyncio.run(inventory.ensure_snapshot(wait=True, **ensure_kwargs))
        return [params for endpoint, params in calls if endpoint == "dcim/locations/"]

    def test_sync_now_only_fetches_changes_since_last_sync(self):
        self._location_queries(force=True)  # first sync: full, sets the watermark
        watermark = asyncio.run(inventory.sync_state("nautobot_inventory"))["last_successful_sync"]
        queries = self._location_queries(force=True, full=False)
        assert queries == [{"last_updated__gte": watermark}]

    def test_force_alone_still_means_full_reconcile(self):
        self._location_queries(force=True)
        queries = self._location_queries(force=True)
        assert queries == [{}]


# ---------------------------------------------------------------------------
# Tests: alert board reads in bulk, not per site (#149)
# ---------------------------------------------------------------------------
class TestAlertBoardBulkReads:
    CHECKED_AT = "2026-09-25T12:00:00+00:00"

    @pytest.fixture(autouse=True)
    def _database(self, monkeypatch, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", adb.async_of(lambda *a, **k: False))
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.CHECKED_AT)
        pass  # the schema: Alembic's (tests/test_schema.py)
        caching.cache.clear()
        yield
        caching.cache.clear()

    def _execute(self, sql, rows):
        adb.many(sql, rows)

    def _seed(self, site_count, down_every=10, backfill_done=True):
        """Seed *site_count* sites with 3 devices; every *down_every*-th site has its router down."""
        self._execute(
            "INSERT INTO nautobot_location_cache (location_id, name, status, location_type, latitude, longitude) "
            "VALUES (%s, %s, 'Active', 'Office', 1.0, 2.0)",
            [(f"loc-{i}", f"Site {i}") for i in range(site_count)],
        )
        self._execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    f"dev-{i}-{j}",
                    f"loc-{i}",
                    f"dev{i}-{j}",
                    "router" if j == 0 else "access",
                    "Offline" if (j == 0 and i % down_every == 0) else "Active",
                    f"10.0.{i % 250}.{j}/32",
                )
                for i in range(site_count)
                for j in range(3)
            ],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-09-25T11:00:00+00:00",
            last_completed_at="2026-09-25T11:00:00+00:00",
            last_successful_sync="2026-09-25T11:00:00+00:00" if backfill_done else None,
            cache_version=inventory.CACHE_VERSION,
            status="idle",
        )

    def _add_alert(self, site_id, device_id, status, down_started_at, total_downtime_seconds=0):
        return adb.sql(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES (%s, %s, '', %s, %s, 'critical', '', %s, %s, %s, %s) RETURNING id",
            (
                # Open alerts use the real key so the build recognises them as still open.
                db.build_alert_key(site_id, device_id)
                if status == "open"
                else f"{site_id}-{device_id}-{down_started_at}",
                site_id,
                device_id,
                device_id,
                status,
                down_started_at,
                down_started_at,
                total_downtime_seconds,
            ),
        )[0]["id"]

    def _build_counting_connections(self):
        opened, counting = adb.counting_connections()
        with (
            counting,
            # The build's own connections; not the build lock or its stats (#295).
            patch.object(alerts, "_board_build_lock", return_value=None),
            patch.object(board_store, "record_board_build"),
        ):
            data = asyncio.run(alerts.get_alert_board_data())
        return data, opened["taken"]

    def test_connection_count_does_not_grow_with_site_count(self):
        self._seed(20)
        small, small_conns = self._build_counting_connections()
        caching.cache.clear()
        self._seed_more(20, 2000)
        large, large_conns = self._build_counting_connections()

        assert small["summary"]["total"] == 20
        assert large["summary"]["total"] == 2000
        assert large["summary"]["critical"] == 200
        # Before #149 a build opened 3 connections per site: 6,000 for 2,000 sites.
        assert large_conns == small_conns
        assert large_conns <= 6

    def _seed_more(self, start, stop):
        self._execute(
            "INSERT INTO nautobot_location_cache (location_id, name, status, location_type, latitude, longitude) "
            "VALUES (%s, %s, 'Active', 'Office', 1.0, 2.0)",
            [(f"loc-{i}", f"Site {i}") for i in range(start, stop)],
        )
        self._execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (
                    f"dev-{i}-{j}",
                    f"loc-{i}",
                    f"dev{i}-{j}",
                    "router" if j == 0 else "access",
                    "Offline" if (j == 0 and i % 10 == 0) else "Active",
                    f"10.0.{i % 250}.{j}/32",
                )
                for i in range(start, stop)
                for j in range(3)
            ],
        )

    def test_bulk_context_matches_per_site_context(self):
        self._seed(6, down_every=3)  # loc-0 and loc-3 have a router down
        # loc-1: healthy now, only closed history -> context comes from the bulk read.
        self._add_alert(
            "loc-1", "dev-1-1", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=900
        )
        # loc-2: healthy now, but an open alert -> it is resolved by the write path.
        self._add_alert("loc-2", "dev-2-2", "open", "2026-09-25T11:30:00+00:00")
        # loc-3: down router with an open alert, a case and closed history.
        open_id = self._add_alert("loc-3", "dev-3-0", "open", "2026-09-25T11:00:00+00:00")
        self._add_alert(
            "loc-3", "dev-3-0", "resolved", "2026-09-20T10:00:00+00:00", total_downtime_seconds=120
        )
        self._execute(
            "INSERT INTO alert_cases (alert_instance_id, case_number, created_at) VALUES (%s, %s, %s)",
            [(open_id, "INC-1", "2026-09-25T11:05:00"), (open_id, "INC-2", "2026-09-25T11:10:00")],
        )

        data = asyncio.run(alerts.get_alert_board_data())

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        for site_id, entry in by_site.items():
            expected = asyncio.run(alerts.get_alert_context_for_site(site_id, self.CHECKED_AT))
            assert (
                entry["historical_downtime_seconds"] == expected["historical_downtime_seconds"]
            ), site_id
            assert entry["current_downtime_seconds"] == expected["current_downtime_seconds"], (
                site_id
            )
            assert entry["active_cases"] == expected["active_cases"], site_id
        assert by_site["loc-1"]["historical_downtime_seconds"] == 900
        assert by_site["loc-2"]["active_alert_instance_count"] == 0  # resolved during the build
        assert by_site["loc-3"]["active_cases"] == ["INC-1", "INC-2"]
        assert by_site["loc-3"]["current_downtime_seconds"] == 3600
        assert by_site["loc-3"]["historical_downtime_seconds"] == 120 + 3600

    def test_sites_and_devices_carry_when_they_went_down(self):
        """The board's default sort and Copy need each down device's start (#227, #228)."""
        self._seed(3, down_every=1)  # every site has its router down
        adb.sql("UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'dev-2-1'")
        self._add_alert("loc-0", "dev-0-0", "open", "2026-09-25T10:00:00+00:00")
        self._add_alert("loc-2", "dev-2-0", "open", "2026-09-25T09:00:00+00:00")
        self._add_alert("loc-2", "dev-2-1", "open", "2026-09-25T11:30:00+00:00")
        # loc-1 has no open alert yet: this build opens one, starting now.

        data = asyncio.run(alerts.get_alert_board_data())

        by_site = {entry["id"]: entry for entry in data["alerts"]}

        def at(value):
            return timeutil.parse_iso_datetime(value)

        assert at(by_site["loc-0"]["latest_down_at"]) == at("2026-09-25T10:00:00Z")
        assert at(by_site["loc-1"]["latest_down_at"]) == at(self.CHECKED_AT)
        assert at(by_site["loc-2"]["latest_down_at"]) == at(
            "2026-09-25T11:30:00Z"
        )  # the newer of two
        started = {
            device["device_id"]: at(device["down_started_at"])
            for device in by_site["loc-2"]["down_devices"]
        }
        assert started == {
            "dev-2-0": at("2026-09-25T09:00:00Z"),
            "dev-2-1": at("2026-09-25T11:30:00Z"),
        }
        assert alerts.empty_alert_context()["latest_down_at"] is None

    def test_excluded_device_status_resolves_its_open_alert(self):
        self._seed(3, down_every=2)  # loc-0 and loc-2: router down (status Offline)
        adb.sql(
            "UPDATE nautobot_device_cache SET status = 'Decommissioning' WHERE device_id = 'dev-0-0'"
        )
        self._add_alert("loc-0", "dev-0-0", "open", "2026-09-25T11:00:00+00:00")

        with overridden(ALERT_BOARD_EXCLUDED_DEVICE_STATUSES={"decommissioning"}):
            data = asyncio.run(alerts.get_alert_board_data())

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        assert by_site["loc-0"]["alert_level"] == "ok"
        assert by_site["loc-0"]["device_count"] == 2
        assert by_site["loc-0"]["active_alert_instance_count"] == 0
        assert by_site["loc-2"]["alert_level"] == "critical"  # Offline is still down
        rows = adb.sql("SELECT status FROM alert_instances WHERE device_id = 'dev-0-0'")
        assert [row["status"] for row in rows] == ["resolved"]

    def test_backfill_pending_skips_writes_and_hides_open_alerts(self):
        self._seed(4, down_every=2, backfill_done=False)
        self._add_alert(
            "loc-1", "dev-1-0", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=300
        )
        self._add_alert("loc-1", "dev-1-2", "open", "2026-09-25T11:00:00+00:00")

        with patch.object(alerts, "upsert_alert_lifecycle_for_site") as upsert:
            data = asyncio.run(alerts.get_alert_board_data())

        upsert.assert_not_called()
        loc1 = next(entry for entry in data["alerts"] if entry["id"] == "loc-1")
        assert loc1["active_alert_instance_count"] == 0
        assert loc1["current_downtime_seconds"] == 0
        assert loc1["historical_downtime_seconds"] == 300 + 3600


# ---------------------------------------------------------------------------
# Tests: one board row per Site, devices rolled up from child locations (#158)
# ---------------------------------------------------------------------------
class TestSiteRollup:
    """EMEA (Region) › DNK (Country) › Aarhus (Site) › Bygning A (Bygning) › Etage 2 (Etage)."""

    LOCATIONS = [
        ("reg-emea", "EMEA", "Region", "", "Active"),
        ("cty-dnk", "DNK", "Country", "reg-emea", "Active"),
        ("site-aar", "Aarhus", "Site", "cty-dnk", "Active"),
        ("bld-a", "Bygning A", "Bygning", "site-aar", "Active"),
        ("flr-2", "Etage 2", "Etage", "bld-a", "Active"),
        ("bld-old", "Bygning Old", "Bygning", "site-aar", "Decommissioning"),
        # A site straight under the region (no country level).
        ("site-osl", "Oslo", "Site", "reg-emea", "Active"),
        # A building with no site above it.
        ("bld-orphan", "Loose Building", "Bygning", "cty-dnk", "Active"),
    ]

    @pytest.fixture(autouse=True)
    def _database(self, monkeypatch, override_settings):
        override_settings(
            NAUTOBOT_URL="https://nautobot.example.com",
            NAUTOBOT_TOKEN="token",
            LIBRENMS_URL="",
            LIBRENMS_API_TOKEN="",
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", adb.async_of(lambda *a, **k: False))
        override_settings(
            ALERT_BOARD_EXCLUDED_LOCATION_STATUSES={"decommissioning"},
            ALERT_BOARD_EXCLUDED_LOCATION_TYPES=set(),
        )
        names = {location_id: name for location_id, name, *_ in self.LOCATIONS}
        adb.many(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, parent_id, parent, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (location_id, name, location_type, parent_id, names.get(parent_id, ""), status)
                for location_id, name, location_type, parent_id, status in self.LOCATIONS
            ],
        )
        adb.many(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                ("d-site", "site-aar", "aar-core01", "core", "Active", "10.1.0.1/32"),
                ("d-bld", "bld-a", "aar-dist01", "distribution", "Active", "10.1.0.2/32"),
                ("d-flr", "flr-2", "aar-acc01", "access", "Offline", "10.1.0.3/32"),
                ("d-old", "bld-old", "aar-old01", "access", "Offline", "10.1.0.4/32"),
                ("d-osl", "site-osl", "osl-core01", "core", "Active", "10.2.0.1/32"),
                ("d-orphan", "bld-orphan", "loose01", "access", "Active", "10.3.0.1/32"),
            ],
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

    def _board(self, monkeypatch, site_type="site"):
        caching.cache.clear()
        with overridden(ALERT_BOARD_SITE_LOCATION_TYPE=site_type):
            return {row["id"]: row for row in asyncio.run(alerts.get_alert_board_data())["alerts"]}

    def test_only_sites_are_rows_and_regions_are_hidden(self, monkeypatch):
        rows = self._board(monkeypatch)
        # The orphan building keeps its own row; Region, Country, Bygning and Etage do not.
        assert set(rows) == {"site-aar", "site-osl", "bld-orphan"}
        assert rows["site-aar"]["ancestor_path"] == "EMEA › DNK"
        assert rows["site-osl"]["ancestor_path"] == "EMEA"

    def test_devices_below_the_site_roll_up(self, monkeypatch):
        aarhus = self._board(monkeypatch)["site-aar"]
        # Site, Bygning A and Etage 2 devices; not the one in the excluded (Decommissioning) building.
        assert aarhus["device_count"] == 3
        assert aarhus["down_device_count"] == 1
        assert aarhus["alert_level"] == "medium"  # 1 of 3 down, an access switch
        assert aarhus["down_devices"] == [
            {
                "device_id": "d-flr",
                "device_name": "aar-acc01",
                "device_ip": "10.1.0.3",
                "status": "Offline",
                "role": "access",
                "case_numbers": [],
                "location_path": "Bygning A › Etage 2",
                "down_started_at": ANY,  # opened by this build, so "now"
            }
        ]

    def test_alerts_are_recorded_on_the_site(self, monkeypatch):
        self._board(monkeypatch)
        rows = adb.sql("SELECT site_id, device_id, status FROM alert_instances")
        assert rows == [{"site_id": "site-aar", "device_id": "d-flr", "status": "open"}]

    def test_setting_is_case_insensitive_and_unset_keeps_one_row_per_location(self, monkeypatch):
        assert "site-aar" in self._board(monkeypatch, site_type="site")
        rows = self._board(monkeypatch, site_type="")
        assert {"reg-emea", "cty-dnk", "bld-a", "flr-2"} <= set(rows)
        assert rows["flr-2"]["down_device_count"] == 1
        # Every row still shows the Nautobot location path above it (#178).
        assert rows["site-aar"]["ancestor_path"] == "EMEA › DNK"
        assert rows["flr-2"]["ancestor_path"] == "EMEA › DNK › Aarhus › Bygning A"
        assert rows["reg-emea"]["ancestor_path"] == ""

    def test_orphan_location_is_logged_once(self, monkeypatch, caplog):
        alerts.logged_rollup_orphans.clear()
        with caplog.at_level("INFO", logger="app.services.alerts_service"):
            self._board(monkeypatch)
            self._board(monkeypatch)
        assert caplog.text.count("'Loose Building' has devices but no 'site' above it") == 1

    def test_parent_cycle_does_not_hang(self):
        locations = [
            {"id": "a", "name": "A", "location_type": "Bygning", "parent_id": "b"},
            {"id": "b", "name": "B", "location_type": "Bygning", "parent_id": "a"},
        ]
        rows, devices = alerts.roll_up_to_site_locations(locations, {"a": [{"id": "d1"}]}, "site")
        assert [row["id"] for row in rows] == ["a"]
        assert devices == {"a": [{"id": "d1", "location_path": ""}]}
        assert [
            row["ancestor_path"] for row in inventory.with_ancestor_paths(locations, locations)
        ] == [
            "A › B",
            "B › A",
        ]


# ---------------------------------------------------------------------------
# Tests: Low and No data levels, all devices down = Critical (#124)
# ---------------------------------------------------------------------------
class TestSeverityTiers:
    @staticmethod
    def _switches(total, down):
        return [
            {
                "id": f"d{i}",
                "name": f"sw{i}",
                "role": "Access Switch",
                "status": "offline" if i < down else "active",
            }
            for i in range(total)
        ]

    @pytest.fixture(autouse=True)
    def _no_database(self, override_settings):
        override_settings(NAUTOBOT_MAPS_DATABASE_URL="")

    @pytest.mark.parametrize(
        ("total", "down", "level", "reason"),
        [
            (20, 0, "ok", ""),
            (20, 1, "low", "1/20 devices offline (5%)"),
            (4, 1, "low", "1/4 devices offline (25%)"),
            (4, 2, "medium", "2/4 devices offline (50%)"),
            (4, 4, "critical", "All 4 monitored devices offline"),
            (1, 1, "critical", "All 1 monitored device offline"),
            (0, 0, "no_data", "No monitored devices"),
        ],
    )
    def test_levels(self, total, down, level, reason):
        assert alerts.compute_alert_level(self._switches(total, down)) == {
            "level": level,
            "reason": reason,
        }

    def test_core_device_still_wins(self):
        devices = self._switches(20, 1)
        devices[0]["role"] = "Core Router"
        assert alerts.compute_alert_level(devices)["level"] == "critical"

    def test_board_order_and_counts(self):
        assert sorted(
            ["ok", "no_data", "low", "critical", "medium"], key=alerts.alert_sort_key
        ) == [
            "critical",
            "medium",
            "low",
            "no_data",
            "ok",
        ]
        levels = ["critical", "low", "low", "no_data", "ok"]
        locations = [{"id": f"loc-{i}", "name": f"Site {i}"} for i in range(len(levels))]

        def fake_alert(location_id, *args, **kwargs):
            return [], {"level": levels[int(location_id.split("-")[1])], "reason": ""}

        caching.cache.clear()
        with (
            patch.object(inventory, "get_locations", return_value=locations),
            patch.object(alerts, "get_location_devices_and_alert", side_effect=fake_alert),
        ):
            summary = asyncio.run(alerts.get_alert_board_data())["summary"]
        assert summary == {
            "total": 5,
            "critical": 1,
            "medium": 0,
            "low": 2,
            "no_data": 1,
            "ok": 1,
            "non_ok": 3,  # No data is not an alert
            "maintenance": 0,
            "wireless": 0,
        }
        assert "unknown" not in summary
