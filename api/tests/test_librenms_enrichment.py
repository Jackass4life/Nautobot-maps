"""LibreNMS on top of Nautobot (#786, from test_app.py): device enrichment, the
polled IP as a fallback, and mappings read in batches."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.clients import caching, librenms, nautobot
from app.clients import http as upstream_http
from app.db import aconnection
from app.repositories import board_repository as board_store
from app.services import alerts_service as alerts
from app.services import inventory_service as inventory
from tests import db as adb
from tests.fakes import snapshot_device, snapshot_location


# ---------------------------------------------------------------------------
# Tests: LibreNMS enrichment
# ---------------------------------------------------------------------------
class TestLibreNMSEnrichment:
    def test_no_enrichment_when_unconfigured(self, override_settings):
        """_enrich_with_librenms is a no-op when LIBRENMS_URL is empty."""
        override_settings(LIBRENMS_URL="", LIBRENMS_API_TOKEN="")
        devices = [{"id": "d1", "name": "router01", "status": "active"}]
        result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result == devices

    def test_fetch_inventory_skips_when_config_is_blank(self, override_settings):
        """_fetch_librenms_inventory skips API calls when URL/token are blank."""
        override_settings(LIBRENMS_URL="   ", LIBRENMS_API_TOKEN="   ")
        with patch.object(upstream_http, "request") as mock_get:
            result = librenms.fetch_inventory()
        assert result == []
        mock_get.assert_not_called()

    def test_librenms_get_uses_verify_setting(self, override_settings):
        override_settings(
            LIBRENMS_URL="https://librenms.test",
            LIBRENMS_API_TOKEN="tok",
            LIBRENMS_VERIFY_SSL=False,
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with patch.object(upstream_http, "request", return_value=mock_resp) as mock_get:
            librenms.get("devices", {"type": "all"})
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False

    def test_librenms_down_overrides_active_status(self, override_settings):
        """A device active in Nautobot but down in LibreNMS is set to offline."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {
            "devices": [
                {"device_id": 1, "hostname": "router01", "status": 0},
            ]
        }
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "offline"

    def test_librenms_up_does_not_change_active_status(self, override_settings):
        """A device up in LibreNMS stays active."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01", "status": 1}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "active"

    def test_librenms_down_does_not_upgrade_already_offline(self, override_settings):
        """A device already offline in Nautobot stays offline (no double-counting)."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "offline"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "offline"

    def test_librenms_api_failure_returns_original_devices(self, override_settings):
        """If LibreNMS API call fails, original device list is returned unchanged."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        with patch.object(librenms, "get", side_effect=Exception("timeout")):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result == devices

    def test_librenms_unmatched_device_not_affected(self, override_settings):
        """Devices not present in LibreNMS are left unchanged."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {"devices": [{"device_id": 1, "hostname": "other-device", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "active"

    def test_librenms_down_matches_short_hostname(self, override_settings):
        """Name-valued LibreNMS hostnames match Nautobot short device names."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01.example.com", "status": 0}]
        }
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ip_when_hostname_is_ip(self, override_settings):
        """IP-valued LibreNMS hostnames match Nautobot primary_ip without mask bits."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {"devices": [{"device_id": 1, "hostname": "192.0.2.1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [
                {"id": "d1", "name": "router01", "primary_ip": "192.0.2.1/32", "status": "active"}
            ]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ipv6_when_hostname_case_differs(self, override_settings):
        """IPv6-valued LibreNMS hostnames match Nautobot primary_ip regardless of hex casing."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {"devices": [{"device_id": 1, "hostname": "2001:DB8::1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [
                {
                    "id": "d1",
                    "name": "router01",
                    "primary_ip": "2001:db8::1/128",
                    "status": "active",
                }
            ]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "offline"

    def test_librenms_ip_hostnames_do_not_collide_with_short_name_keys(self, override_settings):
        """IP-valued LibreNMS hostnames must not be short-name normalized."""
        override_settings(LIBRENMS_URL="http://librenms.test", LIBRENMS_API_TOKEN="tok")
        lnms_response = {"devices": [{"device_id": 1, "hostname": "10.0.0.1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "10", "primary_ip": "192.0.2.5/32", "status": "active"}]
            result = asyncio.run(alerts.enrich_with_librenms(devices))
        assert result[0]["status"] == "active"


# ---------------------------------------------------------------------------
# Tests: LibreNMS polled IP is cached and used as a display fallback (#137)
# ---------------------------------------------------------------------------
# read_librenms_devices: none of LibreNMS's "don't alert" switches set (#570).
OFF = {"disabled": False, "disable_notify": False, "ignore": False}


class TestLibreNMSPolledIp:
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

    def test_sync_caches_polled_ip(self, override_settings):
        override_settings(LIBRENMS_URL="https://librenms.test", LIBRENMS_API_TOKEN="tok")
        pass  # the schema: Alembic's (tests/test_schema.py)
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
            asyncio.run(inventory.sync_librenms(force=True))
        cached = asyncio.run(inventory.read_librenms_devices())
        assert cached == [
            {
                "device_id": 7,
                "hostname": "router01.example.net",
                "ip": "192.0.2.7",
                "status": 1,
                **OFF,
            }
        ]

    def test_device_added_by_hostname_shows_librenms_ip(self, override_settings):
        override_settings(LIBRENMS_URL="https://librenms.test", LIBRENMS_API_TOKEN="tok")
        enriched = asyncio.run(
            alerts.enrich_with_librenms(
                [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}],
                lnms_devices=[
                    {
                        "device_id": 7,
                        "hostname": "router01.example.net",
                        "ip": "192.0.2.7",
                        "status": 1,
                    }
                ],
                lnms_id_map={},
            )
        )
        assert alerts.device_display_ip(enriched[0]) == "192.0.2.7"

    def test_nautobot_primary_ip_still_preferred(self):
        device = {"primary_ip": "10.0.0.1/32", "librenms_ip": "192.0.2.7"}
        assert alerts.device_display_ip(device) == "10.0.0.1"


class TestLibreNMSMappingsAreBatched:
    """Board builds write new LibreNMS mappings once, not a connection per device (#240)."""

    DEVICES = 60

    @pytest.fixture(autouse=True)
    def _inventory(self, monkeypatch, override_settings):
        override_settings(
            NAUTOBOT_URL="",
            NAUTOBOT_TOKEN="",
            LIBRENMS_URL="https://librenms.test",
            LIBRENMS_API_TOKEN="tok",
        )
        # No background sync against the fake LibreNMS URL.
        monkeypatch.setattr(
            inventory, "ensure_snapshot", adb.async_of(lambda *args, **kwargs: False)
        )
        adb.call(inventory.write_locations, [snapshot_location("loc-1", "Big site")])
        adb.call(
            inventory.write_devices,
            [
                {
                    **snapshot_device(f"dev-{n}", "loc-1", "Access Switch", "Active"),
                    "name": f"sw{n}",
                }
                for n in range(self.DEVICES)
            ],
        )
        adb.call(
            inventory.write_librenms_devices,
            [
                # The first device matches by hostname and is down in LibreNMS.
                {
                    "device_id": 1000 + n,
                    "hostname": f"sw{n}.corp.example",
                    "status": 0 if n == 0 else 1,
                }
                for n in range(self.DEVICES)
            ],
        )
        adb.call(
            inventory.record_sync_state,
            "nautobot_inventory",
            last_started_at="2026-01-01T00:00:00Z",
            last_completed_at="2026-01-01T00:01:00Z",
            last_successful_sync="2026-01-01T00:01:00Z",
            status="success",
            error_message="",
        )
        caching.cache.clear()

    def _mappings(self):
        return {
            row["nautobot_device_id"]: row["librenms_device_id"]
            for row in adb.sql("SELECT * FROM librenms_device_map")
        }

    def _count_connections(self):
        return adb.counting_connections()

    def test_board_build_uses_a_few_connections(self):
        counter, patched = self._count_connections()
        with (
            patched,
            patch.object(
                nautobot, "fetch_all_pages", side_effect=AssertionError("no upstream calls")
            ),
        ):
            board = asyncio.run(alerts.get_alert_board_data())
        (site,) = board["alerts"]
        assert site["down_device_count"] == 1
        # Before #240 this was one connection per newly matched device.
        assert counter["taken"] < 15, counter["taken"]
        mappings = self._mappings()
        assert len(mappings) == self.DEVICES
        assert mappings["dev-7"] == 1007

    def test_map_levels_store_mappings_in_one_batch(self):
        # The map reads the board's rows (#316); the scheduler keeps it cached.
        asyncio.run(alerts.cached_alert_board())
        counter, patched = self._count_connections()
        with patched:
            levels = asyncio.run(alerts.build_location_alert_levels())["levels"]
        assert levels["loc-1"]["level"] == "low"
        assert counter["taken"] < 10, counter["taken"]
        assert len(self._mappings()) == self.DEVICES

    def test_persisted_mappings_win_and_are_not_rewritten(self):
        asyncio.run(board_store.store_librenms_maps({"dev-1": (1000, "sw0.corp.example")}))
        with patch.object(
            board_store, "store_librenms_maps", wraps=board_store.store_librenms_maps
        ) as store:
            board = asyncio.run(alerts.get_alert_board_data())
        # dev-1 is mapped to LibreNMS device 1000, which is down.
        down = {d["device_id"] for d in board["alerts"][0]["down_devices"]}
        assert down == {"dev-0", "dev-1"}
        (written,) = [call.args[0] for call in store.call_args_list if call.args[0]]
        assert "dev-1" not in written and len(written) == self.DEVICES - 1

    def test_enrich_on_its_own_writes_once(self):
        devices = [{"id": f"dev-{n}", "name": f"sw{n}", "status": "Active"} for n in range(5)]
        with patch.object(board_store, "store_librenms_maps") as store:
            asyncio.run(alerts.enrich_with_librenms(devices, snapshot_only=True))
        store.assert_called_once()
        assert set(store.call_args.args[0]) == {f"dev-{n}" for n in range(5)}

    def test_a_failed_write_does_not_fail_the_board(self, caplog):
        with patch.object(aconnection, "connect", side_effect=RuntimeError("db gone")):
            asyncio.run(board_store.store_librenms_maps({"dev-1": (1, "sw1")}))
        assert "Could not store 1 LibreNMS device mappings" in caplog.text
