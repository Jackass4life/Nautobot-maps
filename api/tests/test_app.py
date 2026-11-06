"""Ported from the current app's tests/test_app.py (#602); its classes already ported
elsewhere (test_clients.py, test_settings.py, test_schema.py) are left out."""

import importlib
import json
import pathlib
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, patch

import pytest
import requests

from app import db
from app.clients import caching, librenms, nautobot
from app.repositories import board_repository as board_store
from app.services import alerts_service as alerts
from app.services import contract_service as contract
from app.services import inventory_service as inventory
from app.services import maintenance_service as maintenance
from app.services import scheduler
from app.utils import timeutil
from app.utils.legacy_settings import settings

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _full_locations(locations: list[dict]) -> list[dict]:
    """Stand-in locations with a stored location's other fields, empty (#449:
    the board made from them is checked against /api/docs)."""
    # The current tests complete them from the API's answer model (#449), which comes
    # with the routes (step 7 of #587); as they are until then.
    return [dict(location) for location in locations]


@contextmanager
def auth_config(
    mode="disabled",
    user_header="X-Forwarded-User",
    groups_header="X-Forwarded-Groups",
    viewer_groups=None,
    operator_groups=None,
    admin_groups=None,
    default_role="",
):
    saved = {
        "AUTH_MODE": settings.AUTH_MODE,
        "AUTH_HEADER_USER": settings.AUTH_HEADER_USER,
        "AUTH_HEADER_GROUPS": settings.AUTH_HEADER_GROUPS,
        "AUTH_VIEWER_GROUPS": set(settings.AUTH_VIEWER_GROUPS),
        "AUTH_OPERATOR_GROUPS": set(settings.AUTH_OPERATOR_GROUPS),
        "AUTH_ADMIN_GROUPS": set(settings.AUTH_ADMIN_GROUPS),
        "AUTH_DEFAULT_ROLE": settings.AUTH_DEFAULT_ROLE,
    }
    settings.AUTH_MODE = mode
    settings.AUTH_HEADER_USER = user_header
    settings.AUTH_HEADER_GROUPS = groups_header
    settings.AUTH_VIEWER_GROUPS = set(viewer_groups or set())
    settings.AUTH_OPERATOR_GROUPS = set(operator_groups or set())
    settings.AUTH_ADMIN_GROUPS = set(admin_groups or set())
    role = (default_role or "").strip().lower()
    settings.AUTH_DEFAULT_ROLE = role if role in ("viewer", "operator", "admin") else ""
    try:
        yield
    finally:
        settings.AUTH_MODE = saved["AUTH_MODE"]
        settings.AUTH_HEADER_USER = saved["AUTH_HEADER_USER"]
        settings.AUTH_HEADER_GROUPS = saved["AUTH_HEADER_GROUPS"]
        settings.AUTH_VIEWER_GROUPS = saved["AUTH_VIEWER_GROUPS"]
        settings.AUTH_OPERATOR_GROUPS = saved["AUTH_OPERATOR_GROUPS"]
        settings.AUTH_ADMIN_GROUPS = saved["AUTH_ADMIN_GROUPS"]
        settings.AUTH_DEFAULT_ROLE = saved["AUTH_DEFAULT_ROLE"]


@pytest.fixture
def allow_unauthenticated_writes(monkeypatch):
    """The endpoint tests below run with AUTH_MODE=disabled, where the
    administrative writes are refused unless explicitly allowed (#188)."""
    monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)


# ---------------------------------------------------------------------------
# Sample Nautobot API fixtures
# ---------------------------------------------------------------------------
SAMPLE_LOCATIONS_PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {
            "id": "loc-1",
            "name": "Copenhagen DC",
            "slug": "cph-dc",
            "status": {"label": "Active"},
            "location_type": {"name": "Data Center"},
            "country": {"name": "Denmark"},
            "parent": {"name": "Denmark"},
            "latitude": "55.6761",
            "longitude": "12.5683",
            "description": "Main DC",
            "physical_address": "Somestreet 1, Copenhagen",
            "tenant": {
                "id": "ten-1",
                "name": "Acme Corp",
                "tenant_group": {"id": "tg-1", "name": "Corporate"},
            },
            "asn": 65001,
            "time_zone": "Europe/Copenhagen",
            "facility": "CPH-1",
            "tags": [{"name": "critical"}, {"name": "production"}],
            "url": "https://nautobot.example.com/api/dcim/locations/loc-1/",
        },
        {
            "id": "loc-2",
            "name": "Aarhus PoP",
            "slug": "aar-pop",
            "status": {"label": "Planned"},
            "location_type": {"name": "PoP"},
            "parent": None,
            "latitude": "56.1629",
            "longitude": "10.2039",
            "description": "",
            "physical_address": "",
            "tenant": None,
            "asn": None,
            "time_zone": "Europe/Copenhagen",
            "facility": "",
            "tags": [],
            "url": "https://nautobot.example.com/api/dcim/locations/loc-2/",
        },
        # Location without coordinates – should be excluded
        {
            "id": "loc-3",
            "name": "No GPS",
            "slug": "no-gps",
            "status": {"label": "Active"},
            "location_type": {"name": "Office"},
            "parent": None,
            "latitude": None,
            "longitude": None,
            "description": "",
            "physical_address": "",
            "tenant": None,
            "asn": None,
            "time_zone": "",
            "facility": "",
            "tags": [],
            "url": "",
        },
    ],
}

SAMPLE_DEVICES_PAGE = {
    "count": 1,
    "next": None,
    "results": [
        {
            "id": "dev-1",
            "name": "router01",
            "device_type": {
                "model": "ASR1001-X",
                "manufacturer": {"name": "Cisco"},
            },
            "role": {"name": "Core Router"},
            "status": {"label": "Active"},
            "primary_ip4": {"address": "192.0.2.1/32"},
            "platform": {"name": "IOS-XE"},
            "serial": "SN123",
            "tenant": {"name": "Acme Corp"},
        }
    ],
}

SAMPLE_ASNS_PAGE = {
    "count": 1,
    "next": None,
    "results": [
        {
            "asn": 65001,
            "description": "Main ASN",
            "tenant": {"name": "Acme Corp"},
        }
    ],
}


# ---------------------------------------------------------------------------
# Helper – mock nautobot_get to return fixture data
# ---------------------------------------------------------------------------
def mock_nautobot_get(endpoint, params=None, **kwargs):
    params = params or {}
    if "dcim/locations" in endpoint:
        return SAMPLE_LOCATIONS_PAGE
    if "dcim/devices" in endpoint:
        return SAMPLE_DEVICES_PAGE
    if "ipam/asns" in endpoint:
        return SAMPLE_ASNS_PAGE
    if "tenancy/tenant-groups" in endpoint:
        return {
            "count": 1,
            "next": None,
            "results": [{"id": "tg-1", "name": "Corporate"}],
        }
    if "tenancy/tenants" in endpoint:
        return {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "ten-1",
                    "name": "Acme Corp",
                    "tenant_group": {"id": "tg-1", "name": "Corporate"},
                },
            ],
        }
    return {"count": 0, "next": None, "results": []}


# ---------------------------------------------------------------------------
# Tests: pagination helper
# ---------------------------------------------------------------------------


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
class TestApiLocations:
    pass  # its tests are held back (the end of the file says why)


# ---------------------------------------------------------------------------
# Tests: alert board
# ---------------------------------------------------------------------------
class TestAlertBoard:
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
            data = alerts.get_alert_board_data()

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

    def test_location_exclusion_supports_object_tags(self):
        with patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TAGS", {"non-operational"}):
            assert alerts.location_is_excluded_from_alert_board(
                {
                    "name": "Warehouse",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": [{"name": "Non-Operational"}],
                }
            )

    def test_status_exclusion_null_keyword_matches_missing_status(self):
        excluded = settings.parse_csv_set("null, Decommissioning")
        assert alerts.status_is_excluded(None, excluded)
        assert alerts.status_is_excluded("", excluded)
        assert alerts.status_is_excluded("  ", excluded)
        assert alerts.status_is_excluded("DECOMMISSIONING", excluded)
        assert not alerts.status_is_excluded("Active", excluded)
        # Without the keyword an empty status is never excluded.
        assert not alerts.status_is_excluded("", {"decommissioning"})

    def test_location_exclusion_null_status(self):
        with patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", {"null"}):
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
        with patch.object(db, "get_conn", return_value=None):
            scored, alert = alerts.get_location_devices_and_alert(
                "loc-1",
                devices_data=devices,
                devices_already_normalized=True,
                require_primary_ip=True,
                excluded_device_statuses={"decommissioning", "null"},
            )
            unfiltered, unfiltered_alert = alerts.get_location_devices_and_alert(
                "loc-1",
                devices_data=devices,
                devices_already_normalized=True,
                require_primary_ip=True,
            )

        assert [device["name"] for device in scored] == ["sw02"]
        assert alert["level"] == "ok"
        # Without the setting, Decommissioning still counts as a down core device.
        assert len(unfiltered) == 3
        assert unfiltered_alert["level"] == "critical"

    def test_parse_csv_set_normalizes_case_and_whitespace(self):
        assert settings.parse_csv_set(" Decommissioning ; Core Site, POP ") == {
            "decommissioning",
            "core site",
            "pop",
        }

    def test_location_exclusion_matches_mixed_case_configured_values(self):
        with (
            patch.object(
                settings, "ALERT_BOARD_EXCLUDED_LOCATION_NAMES", settings.parse_csv_set("Warehouse")
            ),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES",
                settings.parse_csv_set("Decommissioning"),
            ),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_TYPES",
                settings.parse_csv_set("Branch Office"),
            ),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_TAGS",
                settings.parse_csv_set("Non-Operational"),
            ),
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
            data = alerts.get_alert_board_data()

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
            devices, alert = alerts.get_location_devices_and_alert(
                "loc-1",
                "Data Center",
                snapshot_only=True,
            )

        assert devices == []
        assert alert == {"level": "no_data", "reason": "No monitored devices"}
        ensure_snapshot.assert_not_called()

    def test_location_alert_filters_devices_without_primary_ip(self):
        devices, alert = alerts.get_location_devices_and_alert(
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

        assert [device["id"] for device in devices] == ["dev-2"]
        assert alert == {
            "level": "critical",
            "reason": "Core device(s) offline: router01",
        }

    def test_location_detail_live_device_fetch_retries_with_location_filter_on_400(self):
        bad_request = requests.HTTPError(
            "bad request",
            response=MagicMock(status_code=400),
        )
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
            devices, alert = alerts.get_location_devices_and_alert("loc-1", "Data Center")

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
            data = alerts.get_alert_board_data(force_refresh=True)

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
            patch.object(settings, "LIBRENMS_URL", "https://librenms.example.com"),
            patch.object(settings, "LIBRENMS_API_TOKEN", "token"),
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
            data = alerts.get_alert_board_data(force_refresh=True)

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
            def close(self):
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
            patch.object(db, "get_conn", return_value=_FakeConn()),
            # Connection handling only: no maintenance windows (#283).
            patch.object(
                maintenance,
                "read_active",
                return_value={"sites": {}, "devices": {}, "next_change": None},
            ),
            patch.object(
                inventory,
                "get_sync_state",
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
            data = alerts.get_alert_board_data(force_refresh=True)

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
# Tests: /api/locations/<id>/detail
# ---------------------------------------------------------------------------
class TestApiLocationDetail:
    pass  # its tests are held back (the end of the file says why)

    def _get_detail_with_asn_endpoint_missing(self, client, cached_locations, location_get):
        """Request a detail while ``ipam/asns/`` returns 404 (Nautobot 3.x core)."""
        not_found = requests.HTTPError(
            "404 Client Error: Not Found",
            response=MagicMock(status_code=404),
        )
        with (
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(nautobot, "fetch_all_pages", side_effect=not_found),
            patch.object(inventory, "read_locations", return_value=cached_locations),
            patch.object(nautobot, "get", side_effect=location_get) as mock_get,
        ):
            resp = client.get("/api/locations/loc-1/detail")
        return resp, mock_get


# ---------------------------------------------------------------------------
# Tests: /api/search
# ---------------------------------------------------------------------------
class TestApiSearch:
    pass  # its tests are held back (the end of the file says why)


class TestNautobotRuntimeErrors:
    pass  # its tests are held back (the end of the file says why)


# ---------------------------------------------------------------------------
# Tests: index page
# ---------------------------------------------------------------------------
class TestIndex:
    """The map: built with Nuxt (#463); frontend/tests/ and e2e/ test the page itself."""

    def test_live_locations_have_their_path(self, monkeypatch):
        """Without the cache (a map-only setup), /api/locations' paths still come along (#367)."""
        import mock_nautobot

        monkeypatch.setattr(
            inventory, "read_locations", lambda include_without_coordinates=False, conn=None: []
        )
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda **kwargs: None)
        monkeypatch.setattr(inventory, "live_tenant_descriptions", lambda: {})
        monkeypatch.setattr(
            nautobot, "fetch_all_pages", lambda path, **kwargs: list(mock_nautobot.LOCATIONS)
        )
        everywhere = {
            loc["id"]: loc for loc in inventory.get_locations(include_without_coordinates=True)
        }
        assert (
            everywhere["loc-aar-bld-a-f2"]["ancestor_path"]
            == "EMEA › DNK › Aarhus Campus › Bygning A"
        )
        assert {loc["id"]: loc["ancestor_path"] for loc in inventory.get_locations()}[
            "loc-cph"
        ] == ""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", None),
            ("55.7, 12.6, 6", (55.7, 12.6, 6)),
            ("-33.9,151.2,10", (-33.9, 151.2, 10)),
            ("55.7,12.6", None),
            ("55.7,12.6,6,1", None),
            ("91,0,5", None),
            ("0,181,5", None),
            ("55.7,12.6,0", None),
            ("55.7,12.6,20", None),
            ("55.7,12.6,6.5", None),
            ("Copenhagen", None),
            ("nan,0,5", None),
        ],
    )
    def test_default_view_is_checked(self, raw, expected, caplog):
        assert settings._map_view(raw) == expected
        assert ("MAP_DEFAULT_VIEW ignored" in caplog.text) == (bool(raw) and expected is None)


# ---------------------------------------------------------------------------
# Tests: caching
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: NAUTOBOT_URL validation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: SSL verification configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: Accept header / API version configuration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: Custom error handlers
# ---------------------------------------------------------------------------
class TestErrorHandlers:
    pass  # its tests are held back (the end of the file says why)


# ---------------------------------------------------------------------------
# Tests: configurable critical role keywords
# ---------------------------------------------------------------------------
class TestConfigurableCriticalKeywords:
    """Tests for the _get_critical_keywords / compute_alert_level helpers."""

    def setup_method(self):
        """Reset module-level keyword state before each test."""
        self._orig_env_kw = alerts.ENV_CORE_ROLE_KEYWORDS
        self._orig_rules = dict(alerts.CRITICALITY_RULES)

    def teardown_method(self):
        alerts.ENV_CORE_ROLE_KEYWORDS = self._orig_env_kw
        alerts.CRITICALITY_RULES = self._orig_rules

    def test_default_keywords_applied(self):
        """Without any configuration the built-in defaults are used."""
        alerts.ENV_CORE_ROLE_KEYWORDS = alerts.DEFAULT_CORE_ROLE_KEYWORDS
        alerts.CRITICALITY_RULES = {}
        kw = alerts.get_critical_keywords()
        assert "core" in kw
        assert "router" in kw

    def test_env_override_replaces_defaults(self):
        """_ENV_CORE_ROLE_KEYWORDS env override replaces defaults when no JSON rules."""
        alerts.ENV_CORE_ROLE_KEYWORDS = ("firewall", "border")
        alerts.CRITICALITY_RULES = {}
        kw = alerts.get_critical_keywords()
        assert kw == ("firewall", "border")

    def test_env_override_used_as_fallback_for_unknown_type(self):
        """When rules have no matching type and no 'default' key, env override is used."""
        alerts.ENV_CORE_ROLE_KEYWORDS = ("firewall",)
        alerts.CRITICALITY_RULES = {"datacenter": ["core", "spine"]}
        kw = alerts.get_critical_keywords("office")
        assert kw == ("firewall",)

    def test_location_type_rule_matched(self):
        """The exact location_type key is returned when present in rules."""
        alerts.CRITICALITY_RULES = {
            "datacenter": ["core", "firewall"],
            "office": ["router"],
        }
        kw = alerts.get_critical_keywords("Datacenter")
        assert "firewall" in kw

    def test_rules_default_key_used_for_unknown_type(self):
        """The 'default' key in rules is the fallback for unknown location types."""
        alerts.CRITICALITY_RULES = {
            "default": ["core", "spine"],
            "office": ["router"],
        }
        kw = alerts.get_critical_keywords("warehouse")
        assert kw == ("core", "spine")

    def test_compute_alert_level_respects_location_type(self):
        """compute_alert_level uses the correct keyword set for the given location type."""
        alerts.CRITICALITY_RULES = {
            "office": ["router"],
            "datacenter": ["core", "firewall"],
        }
        # A "firewall" device offline in a datacenter → critical
        dc_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"}]
        result = alerts.compute_alert_level(dc_devices, location_type="datacenter")
        assert result["level"] == "critical"

        # Same device in an office (only "router" is critical there) → medium (if >25%) or ok
        office_devices = [
            {"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"},
            {"id": "d2", "name": "sw01", "role": "Switch", "status": "active"},
        ]
        result = alerts.compute_alert_level(office_devices, location_type="office")
        assert result["level"] != "critical"

    def test_compute_alert_level_no_devices(self):
        assert alerts.compute_alert_level([]) == {
            "level": "no_data",
            "reason": "No monitored devices",
        }

    def test_compute_alert_level_medium_threshold(self):
        """More than 25% of devices down → medium alert."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
        ]
        # 1/3 ≈ 33% > 25% → medium
        result = alerts.compute_alert_level(devices)
        assert result["level"] == "medium"

    def test_compute_alert_level_low_when_below_threshold(self):
        """Under 25% down and no core device down → ok."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
            {"id": "d4", "name": "sw04", "role": "Switch", "status": "active"},
            {"id": "d5", "name": "sw05", "role": "Switch", "status": "active"},
        ]
        # 1/5 = 20% ≤ 25% → ok
        result = alerts.compute_alert_level(devices)
        assert result == {"level": "low", "reason": "1/5 devices offline (20%)"}


# ---------------------------------------------------------------------------
# Tests: location_type passed through detail endpoint
# ---------------------------------------------------------------------------
class TestLocationDetailWithLocationType:
    pass  # its tests are held back (the end of the file says why)


# ---------------------------------------------------------------------------
# Tests: criticality override REST endpoints
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("allow_unauthenticated_writes")
class TestCriticalityOverrideEndpoints:
    """Tests for /api/criticality-overrides (requires the persistence database)."""

    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def test_override_affects_compute_alert_level(self):
        """A device marked is_critical=False must not trigger a critical alert."""
        self.db.execute(
            "INSERT INTO device_criticality_override "
            "(nautobot_device_id, is_critical, reason, updated_by) "
            "VALUES (%s, %s, %s, %s)",
            ("dev-fw", 0, "Local firewall – not critical", "test"),
        )

        devices = [
            {"id": "dev-fw", "name": "fw-local", "role": "Core Router", "status": "offline"},
            # A healthy second device, so "every device down" (also Critical) does not apply.
            {"id": "dev-sw", "name": "sw-local", "role": "Switch", "status": "active"},
        ]
        # The override says is_critical=False, so even a "Core Router" that's
        # offline should not produce a critical alert.
        result = alerts.compute_alert_level(devices)
        assert result["level"] != "critical"


# ---------------------------------------------------------------------------
# Tests: alert lifecycle history and cases
# ---------------------------------------------------------------------------
class TestAlertLifecycleTracking:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def test_severity_change_keeps_the_alert_and_its_downtime(self):
        """Medium → Critical must not restart the device's downtime (#163)."""
        site = {"id": "loc-1", "name": "Site One"}
        t0 = "2026-01-01T00:00:00Z"
        t1 = "2026-01-01T01:00:00Z"
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "sw01", "status": "offline"}],
            {"level": "medium", "reason": "1/3 devices offline (33%)"},
            t0,
        )
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [
                {"id": "dev-1", "name": "sw01", "status": "offline"},
                {"id": "dev-2", "name": "core01", "status": "offline"},
            ],
            {"level": "critical", "reason": "Core device(s) offline: core01"},
            t1,
        )

        rows = self.db.execute(
            "SELECT device_id, status, alert_level, down_started_at FROM alert_instances ORDER BY device_id, id"
        )
        dev1 = [row for row in rows if row["device_id"] == "dev-1"]
        assert len(dev1) == 1, dev1  # one alert, not resolved and reopened
        assert dev1[0]["status"] == "open"
        assert dev1[0]["alert_level"] == "critical"
        assert db.serialize_value(dev1[0]["down_started_at"]) == t0
        events = self.db.execute(
            "SELECT e.event_type, e.alert_level FROM alert_events e "
            "JOIN alert_instances i ON i.id = e.alert_instance_id WHERE i.device_id = 'dev-1' ORDER BY e.id"
        )
        assert events == [
            {"event_type": "opened", "alert_level": "medium"},
            {"event_type": "updated", "alert_level": "critical"},
        ]
        context = alerts.get_alert_context_for_site("loc-1", t1)
        assert context["current_downtime_seconds"] == 3600

    def test_failed_alert_observation_does_not_resolve_open_incident(self):
        site = {"id": "loc-1", "name": "Site One"}
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "router01", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        with (
            patch.object(
                inventory, "get_locations", return_value=_full_locations(sample_locations)
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")
            ),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)
        assert data["alerts"][0]["alert_level"] == "no_data"
        history = alerts.get_alert_context_for_site("loc-1", timeutil.iso_utc_now())
        assert history["active_alert_instance_count"] == 1
        conn = db.get_conn()
        try:
            rows = conn.execute(
                "SELECT status FROM alert_instances WHERE site_id = %s ORDER BY id DESC",
                ("loc-1",),
            ).fetchall()
        finally:
            conn.close()
        assert db.row_to_dict(rows[0])["status"] == "open"

    def test_get_alert_board_data_uses_cache_when_persistence_enabled(self):
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        devices_return = (
            [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
        )
        with (
            patch.object(
                inventory, "get_locations", return_value=sample_locations
            ) as get_locations,
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts, "get_location_devices_and_alert", return_value=devices_return
            ) as get_alert,
        ):
            first = alerts.get_alert_board_data(force_refresh=True)
            second = alerts.get_alert_board_data()
        assert first["alerts"] == second["alerts"]
        assert get_locations.call_count == 1
        assert get_alert.call_count == 1

    def test_get_alert_board_data_sets_ttl_and_enqueues_sync_on_force_refresh(self):
        first_payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        second_payload = {
            "checked_at": "2026-01-01T00:05:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        caching.cache.clear()
        with (
            patch.object(
                alerts,
                "build_alert_board_payload",
                side_effect=[first_payload, second_payload],
            ) as build_payload,
            patch.object(caching, "set", wraps=caching.set) as cache_set,
            patch.object(
                inventory,
                "ensure_snapshot",
            ) as ensure_snapshot,
            patch.object(
                inventory,
                "board_snapshot",
                return_value={"version": None, "initialized": True, "nautobot": {}},
            ),
        ):
            first = alerts.get_alert_board_data(force_refresh=True)
            second = alerts.get_alert_board_data()
            refreshed = alerts.get_alert_board_data(force_refresh=True)
        assert first["checked_at"] == second["checked_at"] == "2026-01-01T00:00:00Z"
        assert refreshed["checked_at"] == "2026-01-01T00:00:00Z"
        assert build_payload.call_count == 1
        assert cache_set.call_count == 1
        assert ensure_snapshot.call_count == 2
        assert all(
            call.args == ()
            and call.kwargs
            == {
                "force": True,
                "full": False,
                "wait": False,
                "cooldown_seconds": inventory.FORCED_SYNC_COOLDOWN_SECONDS,
            }
            for call in ensure_snapshot.call_args_list
        )
        # The stored board outlives the sync interval a little (#295).
        assert all(
            call.kwargs.get("timeout") == settings.CACHE_TTL + alerts.BOARD_TTL_SLACK_SECONDS
            for call in cache_set.call_args_list
        )

    def test_get_alert_board_data_builds_snapshot_only_payload(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(
                alerts, "build_alert_board_payload", return_value=payload
            ) as build_payload,
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
        ):
            result = alerts.get_alert_board_data(force_refresh=True)

        assert result["checked_at"] == "2026-01-01T00:00:00Z"
        build_payload.assert_called_once_with(snapshot_only=True)
        ensure_snapshot.assert_called_once_with(
            force=True,
            full=False,
            wait=False,
            cooldown_seconds=inventory.FORCED_SYNC_COOLDOWN_SECONDS,
        )

    def test_get_alert_board_data_does_not_cache_empty_payload_before_snapshot_init(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(
                alerts,
                "build_alert_board_payload",
                return_value=payload,
            ) as build_payload,
            patch.object(
                caching,
                "set",
                wraps=caching.set,
            ) as cache_set,
            patch.object(
                inventory,
                "board_snapshot",
                return_value={"version": None, "initialized": False, "nautobot": {}},
            ),
            patch.object(inventory, "ensure_snapshot"),
        ):
            first = alerts.get_alert_board_data(force_refresh=True)
            second = alerts.get_alert_board_data()

        assert first["alerts"] == []
        assert second["alerts"] == []
        assert build_payload.call_count == 2
        assert cache_set.call_count == 0

    def test_postgres_lifecycle_path_uses_postgres_sql(self):
        class _FakeResult:
            def __init__(self, rows=None, rowcount=0):
                self._rows = rows or []
                self.rowcount = rowcount

            def fetchone(self):
                return self._rows[0] if self._rows else None

            def fetchall(self):
                return self._rows

        class _FakeTransaction:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                self.conn.transaction_entries += 1
                return self.conn

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeConn:
            def __init__(self):
                self.queries = []
                self.closed = False
                self.connection_context_entries = 0
                self.transaction_entries = 0

            def __enter__(self):
                self.connection_context_entries += 1
                return self

            def __exit__(self, exc_type, exc, tb):
                self.closed = True
                return False

            def close(self):
                self.closed = True

            def transaction(self):
                return _FakeTransaction(self)

            def execute(self, query, params=()):
                if self.closed:
                    raise RuntimeError("the connection is closed")
                self.queries.append((query, params))
                if "SELECT id, status, down_started_at, alert_level, alert_reason" in query:
                    return _FakeResult([])
                if "INSERT INTO alert_instances" in query:
                    return _FakeResult([{"id": 101}])
                if "INSERT INTO alert_events" in query:
                    return _FakeResult([])
                if (
                    "SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at"
                    in query
                ):
                    return _FakeResult(
                        [
                            {
                                "id": 101,
                                "alert_key": "k",
                                "site_id": "loc-1",
                                "site_name": "Site One",
                                "device_id": "dev-1",
                                "device_name": "router01",
                                "down_started_at": "2026-01-01T00:00:00Z",
                            }
                        ]
                    )
                if "UPDATE alert_instances" in query:
                    return _FakeResult([], rowcount=1)
                return _FakeResult([])

        fake_conn = _FakeConn()
        checked_at = "2026-01-01T00:00:00Z"
        with patch.object(db, "dialect", return_value="postgres"):
            alerts.upsert_alert_lifecycle_for_site(
                {"id": "loc-1", "name": "Site One"},
                [{"id": "dev-1", "name": "router01", "status": "offline"}],
                {"level": "critical", "reason": "offline"},
                checked_at,
                conn=fake_conn,
            )
            alerts.resolve_open_alert_instances_for_site(
                fake_conn,
                "loc-1",
                set(),
                checked_at,
            )
        insert_sql = next(
            query for query, _ in fake_conn.queries if "INSERT INTO alert_instances" in query
        )
        assert "ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING" in insert_sql
        assert "%s" in insert_sql
        update_sql = next(
            query for query, _ in fake_conn.queries if "UPDATE alert_instances" in query
        )
        assert "CURRENT_TIMESTAMP" in update_sql
        assert "AND status = 'open'" in update_sql
        assert "AND down_started_at <=" in update_sql
        event_params = [
            params for query, params in fake_conn.queries if "INSERT INTO alert_events" in query
        ]
        assert any(param == checked_at for params in event_params for param in params)
        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 1


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

    def test_alert_board_uses_cached_inventory_snapshot(self):
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
                inventory.write_devices(
                    conn,
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
        finally:
            conn.close()

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["id"] == "loc-1"
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][0]["alert_level"] == "critical"

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

    def test_primary_ip_backfill_pending_until_current_cache_version_sync(self):
        with patch.object(
            inventory,
            "get_sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": "1",
            },
        ):
            assert board_store.nautobot_inventory_primary_ip_backfill_pending() is True

        with patch.object(
            inventory,
            "get_sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": inventory.CACHE_VERSION,
            },
        ):
            assert board_store.nautobot_inventory_primary_ip_backfill_pending() is False

    def test_alert_board_filters_cached_devices_without_primary_ip_while_backfill_is_pending(self):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
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
                inventory.write_devices(
                    conn,
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
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at=None,
                    last_completed_at=None,
                    last_successful_sync=None,
                    status="pending",
                    error_message="",
                )
        finally:
            conn.close()

        caching.cache.clear()
        with patch.object(
            nautobot,
            "fetch_all_pages",
            side_effect=AssertionError("should not fetch live inventory"),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["no_data"] == 1  # no monitored devices yet
        assert data["alerts"][0]["device_count"] == 0
        assert data["alerts"][0]["down_device_count"] == 0

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
class TestCriticalityRulesFile:
    def test_load_valid_rules_file(self, tmp_path):
        """A valid JSON rules file is parsed into _CRITICALITY_RULES."""
        rules = {"datacenter": ["core", "firewall"], "office": ["router"]}
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        orig = dict(alerts.CRITICALITY_RULES)
        orig_file = settings.CRITICALITY_RULES_FILE
        try:
            settings.CRITICALITY_RULES_FILE = str(rules_file)
            # Re-run the loading logic
            with open(str(rules_file)) as f:
                loaded = json.load(f)
            alerts.CRITICALITY_RULES = {
                k.lower(): [kw.lower() for kw in v]
                for k, v in loaded.items()
                if isinstance(v, list)
            }
            assert alerts.get_critical_keywords("datacenter") == ("core", "firewall")
            assert alerts.get_critical_keywords("office") == ("router",)
        finally:
            alerts.CRITICALITY_RULES = orig
            settings.CRITICALITY_RULES_FILE = orig_file

    def test_rules_file_bad_format_ignored(self, tmp_path):
        """A rules file with a non-dict top level is ignored gracefully."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps(["not", "a", "dict"]))

        orig = dict(alerts.CRITICALITY_RULES)
        try:
            # Simulate what the loading code does
            with open(str(bad_file)) as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                pass  # Would be ignored in real code
            # _CRITICALITY_RULES should remain unchanged
            assert alerts.CRITICALITY_RULES == orig
        finally:
            alerts.CRITICALITY_RULES = orig


# ---------------------------------------------------------------------------
# Tests: LibreNMS enrichment
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

    def test_no_enrichment_when_unconfigured(self):
        """_enrich_with_librenms is a no-op when LIBRENMS_URL is empty."""
        settings.LIBRENMS_URL = ""
        settings.LIBRENMS_API_TOKEN = ""
        devices = [{"id": "d1", "name": "router01", "status": "active"}]
        result = alerts.enrich_with_librenms(devices)
        assert result == devices

    def test_fetch_inventory_skips_when_config_is_blank(self):
        """_fetch_librenms_inventory skips API calls when URL/token are blank."""
        settings.LIBRENMS_URL = "   "
        settings.LIBRENMS_API_TOKEN = "   "
        with patch.object(requests.Session, "get") as mock_get:
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
        with patch.object(requests.Session, "get", return_value=mock_resp) as mock_get:
            librenms.get("devices", {"type": "all"})
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False

    def test_librenms_get_suppresses_warning_locally_when_verify_disabled(self):
        settings.LIBRENMS_URL = "https://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        settings.LIBRENMS_VERIFY_SSL = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with (
            patch.object(requests.Session, "get", return_value=mock_resp),
            patch.object(librenms.warnings, "catch_warnings") as mock_catch,
        ):
            librenms.get("devices", {"type": "all"})
        mock_catch.assert_called_once()

    def test_librenms_down_overrides_active_status(self):
        """A device active in Nautobot but down in LibreNMS is set to offline."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [
                {"device_id": 1, "hostname": "router01", "status": 0},
            ]
        }
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_up_does_not_change_active_status(self):
        """A device up in LibreNMS stays active."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01", "status": 1}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "active"

    def test_librenms_down_does_not_upgrade_already_offline(self):
        """A device already offline in Nautobot stays offline (no double-counting)."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "offline"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_api_failure_returns_original_devices(self):
        """If LibreNMS API call fails, original device list is returned unchanged."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        with patch.object(librenms, "get", side_effect=Exception("timeout")):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result == devices

    def test_librenms_unmatched_device_not_affected(self):
        """Devices not present in LibreNMS are left unchanged."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "other-device", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "active"

    def test_librenms_down_matches_short_hostname(self):
        """Name-valued LibreNMS hostnames match Nautobot short device names."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01.example.com", "status": 0}]
        }
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ip_when_hostname_is_ip(self):
        """IP-valued LibreNMS hostnames match Nautobot primary_ip without mask bits."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "192.0.2.1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [
                {"id": "d1", "name": "router01", "primary_ip": "192.0.2.1/32", "status": "active"}
            ]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ipv6_when_hostname_case_differs(self):
        """IPv6-valued LibreNMS hostnames match Nautobot primary_ip regardless of hex casing."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
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
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_ip_hostnames_do_not_collide_with_short_name_keys(self):
        """IP-valued LibreNMS hostnames must not be short-name normalized."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "10.0.0.1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "10", "primary_ip": "192.0.2.5/32", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "active"


class TestAuthConfiguration:
    @staticmethod
    def _reload_gunicorn_config():
        import gunicorn_config

        return importlib.reload(gunicorn_config)

    def test_default_trusts_only_localhost(self):
        assert [str(n) for n in settings._networks("t", "127.0.0.1/32,::1/128")] == [
            "127.0.0.1/32",
            "::1/128",
        ]
        with pytest.raises(RuntimeError, match="AUTH_TRUSTED_PROXIES"):
            settings._networks("AUTH_TRUSTED_PROXIES", "10.0.0.0/33")

    def test_the_nautobot_client_cannot_write(self):
        assert not hasattr(nautobot, "post") and not hasattr(nautobot, "delete")


# ---------------------------------------------------------------------------
# Tests: alert-board tier definitions and per-device IP (#117)
# ---------------------------------------------------------------------------
class TestAlertBoardTierDefinitions:
    pass  # its tests are held back (the end of the file says why)


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
        settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN = "https://librenms.test", "tok"
        try:
            devices = [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}]
            enriched = alerts.enrich_with_librenms(
                devices,
                lnms_devices=[{"device_id": 7, "hostname": "router01", "status": 1}],
                lnms_id_map={},
            )
        finally:
            settings.LIBRENMS_URL, settings.LIBRENMS_API_TOKEN = orig_url, orig_token
        assert enriched[0]["librenms_hostname"] == "router01"


# ---------------------------------------------------------------------------
# Tests: alert-board refresh, cold start and sync progress (#121)
# ---------------------------------------------------------------------------
class TestAlertBoardSyncProgress:
    @pytest.fixture(autouse=True)
    def _persistence(self, pg_database):
        caching.cache.clear()
        yield
        caching.cache.clear()

    def _set_nautobot_sync_state(self, status, started_at, completed_at=None):
        conn = db.get_conn()
        try:
            with conn:
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at=started_at,
                    last_completed_at=completed_at,
                    last_successful_sync=completed_at,
                    cache_version=inventory.CACHE_VERSION,
                    status=status,
                    error_message="",
                )
        finally:
            conn.close()

    def _board(self, alerts=None):
        board = {
            "checked_at": timeutil.iso_utc_now(),
            "stale_after_seconds": 300,
            "summary": {"total": len(alerts or [])},
            "alerts": alerts or [],
        }
        # The current tests complete it from the API's answer model (#449), which comes with
        # the routes (step 7 of #587); as it is until then.
        return board

    def test_next_update_uses_the_sooner_source(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "token")
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 3600)
        monkeypatch.setattr(settings, "LIBRENMS_SYNC_INTERVAL_SECONDS", 120)
        now = datetime.now(UTC).isoformat()
        self._set_nautobot_sync_state("idle", now, now)
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "librenms_inventory",
                last_started_at=now,
                last_completed_at=now,
                status="idle",
            )
        conn.close()
        due, next_in = alerts.inventory_update_schedule()
        assert due is False
        assert 115 <= next_in <= 120

    def test_next_update_unknown_while_sync_runs(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        self._set_nautobot_sync_state("running", timeutil.iso_utc_now())
        assert alerts.inventory_update_schedule() == (False, None)

    def test_next_update_unknown_without_persistence(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert alerts.inventory_update_schedule() == (False, None)

    def test_abandoned_running_sync_is_not_pending(self):
        started = datetime(2020, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        self._set_nautobot_sync_state("running", started)
        assert alerts.nautobot_sync_in_progress() is False

    def test_no_sync_pending_without_persistence(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert alerts.nautobot_sync_in_progress() is False


# ---------------------------------------------------------------------------
# Tests: one case number on multiple devices (#133)
# ---------------------------------------------------------------------------
class TestMultiDeviceCases:
    @pytest.fixture(autouse=True)
    def _database_with_open_alerts(self, pg_database):
        alerts.upsert_alert_lifecycle_for_site(
            {"id": "loc-1", "name": "Site One"},
            [
                {"id": "dev-1", "name": "sw01", "status": "offline"},
                {"id": "dev-2", "name": "sw02", "status": "offline"},
                {"id": "dev-3", "name": "sw03", "status": "offline"},
            ],
            {"level": "medium", "reason": "3/4 devices offline (75%)"},
            timeutil.iso_utc_now(),
        )
        yield
        caching.cache.clear()

    def _cases_for(self, client, device_id):
        history = client.get(f"/api/alert-history?site_id=loc-1&device_id={device_id}").get_json()
        return [case["case_number"] for case in history["instances"][0]["cases"]]


# Tests: /healthz (#131)
# ---------------------------------------------------------------------------
class TestHealthz:
    pass  # its tests are held back (the end of the file says why)


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

    def test_sync_caches_polled_ip(self, monkeypatch, pg_database):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
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

    def test_device_added_by_hostname_shows_librenms_ip(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        enriched = alerts.enrich_with_librenms(
            [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}],
            lnms_devices=[
                {"device_id": 7, "hostname": "router01.example.net", "ip": "192.0.2.7", "status": 1}
            ],
            lnms_id_map={},
        )
        assert alerts.device_display_ip(enriched[0]) == "192.0.2.7"

    def test_nautobot_primary_ip_still_preferred(self):
        device = {"primary_ip": "10.0.0.1/32", "librenms_ip": "192.0.2.7"}
        assert alerts.device_display_ip(device) == "10.0.0.1"


# Tests: alert board explains a missing persistence database (#136)
# ---------------------------------------------------------------------------
class TestAlertBoardWithoutPersistence:
    pass  # its tests are held back (the end of the file says why)


# Tests: Refresh runs an incremental "sync now", not a full reconcile (#135)
# ---------------------------------------------------------------------------
class TestRefreshIsIncremental:
    @pytest.fixture(autouse=True)
    def _database_and_nautobot(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        pass  # the schema: Alembic's (tests/test_schema.py)

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
class TestAlertBoardBulkReads:
    CHECKED_AT = "2026-09-25T12:00:00+00:00"

    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.CHECKED_AT)
        pass  # the schema: Alembic's (tests/test_schema.py)
        caching.cache.clear()
        yield
        caching.cache.clear()

    def _execute(self, sql, rows):
        self.db.executemany(sql, rows)

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
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-25T11:00:00+00:00",
                last_completed_at="2026-09-25T11:00:00+00:00",
                last_successful_sync="2026-09-25T11:00:00+00:00" if backfill_done else None,
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

    def _add_alert(self, site_id, device_id, status, down_started_at, total_downtime_seconds=0):
        return self.db.execute(
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
        opened = []
        real_get_db_conn = db.get_conn

        def counting_get_db_conn():
            opened.append(1)
            return real_get_db_conn()

        with (
            patch.object(db, "get_conn", side_effect=counting_get_db_conn),
            # The build's own connections; not the build lock or its stats (#295).
            patch.object(alerts, "_board_build_lock", return_value=None),
            patch.object(board_store, "record_board_build"),
        ):
            data = alerts.get_alert_board_data()
        return data, len(opened)

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

        data = alerts.get_alert_board_data()

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        for site_id, entry in by_site.items():
            expected = alerts.get_alert_context_for_site(site_id, self.CHECKED_AT)
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
        self.db.execute(
            "UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'dev-2-1'"
        )
        self._add_alert("loc-0", "dev-0-0", "open", "2026-09-25T10:00:00+00:00")
        self._add_alert("loc-2", "dev-2-0", "open", "2026-09-25T09:00:00+00:00")
        self._add_alert("loc-2", "dev-2-1", "open", "2026-09-25T11:30:00+00:00")
        # loc-1 has no open alert yet: this build opens one, starting now.

        data = alerts.get_alert_board_data()

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
        self.db.execute(
            "UPDATE nautobot_device_cache SET status = 'Decommissioning' WHERE device_id = 'dev-0-0'"
        )
        self._add_alert("loc-0", "dev-0-0", "open", "2026-09-25T11:00:00+00:00")

        with patch.object(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"decommissioning"}):
            data = alerts.get_alert_board_data()

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        assert by_site["loc-0"]["alert_level"] == "ok"
        assert by_site["loc-0"]["device_count"] == 2
        assert by_site["loc-0"]["active_alert_instance_count"] == 0
        assert by_site["loc-2"]["alert_level"] == "critical"  # Offline is still down
        rows = self.db.execute("SELECT status FROM alert_instances WHERE device_id = 'dev-0-0'")
        assert [row["status"] for row in rows] == ["resolved"]

    def test_backfill_pending_skips_writes_and_hides_open_alerts(self):
        self._seed(4, down_every=2, backfill_done=False)
        self._add_alert(
            "loc-1", "dev-1-0", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=300
        )
        self._add_alert("loc-1", "dev-1-2", "open", "2026-09-25T11:00:00+00:00")

        with patch.object(alerts, "upsert_alert_lifecycle_for_site") as upsert:
            data = alerts.get_alert_board_data()

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
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", {"decommissioning"})
        monkeypatch.setattr(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TYPES", set())
        names = {location_id: name for location_id, name, *_ in self.LOCATIONS}
        self.db.executemany(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, parent_id, parent, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [
                (location_id, name, location_type, parent_id, names.get(parent_id, ""), status)
                for location_id, name, location_type, parent_id, status in self.LOCATIONS
            ],
        )
        self.db.executemany(
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
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-25T11:00:00+00:00",
                last_completed_at="2026-09-25T11:00:00+00:00",
                last_successful_sync="2026-09-25T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

    def _board(self, monkeypatch, site_type="site"):
        monkeypatch.setattr(settings, "ALERT_BOARD_SITE_LOCATION_TYPE", site_type)
        caching.cache.clear()
        return {row["id"]: row for row in alerts.get_alert_board_data()["alerts"]}

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
        rows = self.db.execute("SELECT site_id, device_id, status FROM alert_instances")
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
# Tests: background scheduler records history with nobody viewing (#154)
# ---------------------------------------------------------------------------
class TestBackgroundScheduler:
    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        self.db = pg_database
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        self.db.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        self.db.execute(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES ('dev-1', 'loc-1', 'router01', 'router', 'Offline', '10.0.0.1/32')"
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-25T11:00:00+00:00",
                last_completed_at="2026-09-25T11:00:00+00:00",
                last_successful_sync="2026-09-25T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

    def test_tick_after_a_sync_records_alert_history_without_any_request(self):
        with patch.object(inventory, "ensure_snapshot", return_value=True) as ensure:
            assert scheduler.tick() is True
        ensure.assert_called_once_with(wait=True)
        rows = self.db.execute("SELECT site_id, device_id, status FROM alert_instances")
        assert rows == [{"site_id": "loc-1", "device_id": "dev-1", "status": "open"}]
        # The rebuilt board is cached, so the next page load is instant.
        assert caching.cache.get("alert-board-data:v3")["summary"]["critical"] == 1

    def test_tick_without_a_due_sync_does_nothing(self):
        with (
            patch.object(inventory, "ensure_snapshot", return_value=False),
            patch.object(alerts, "build_alert_board_payload") as build,
        ):
            assert scheduler.tick() is False
        build.assert_not_called()

    def test_only_one_process_ticks_at_a_time(self):
        """Another worker holding the lock makes this tick skip."""
        release = db.try_advisory_lock("background_scheduler")
        assert callable(release)
        try:
            other = db.try_advisory_lock("background_scheduler")
            assert other is False  # a second connection cannot take it
            with patch.object(inventory, "ensure_snapshot") as ensure:
                assert scheduler.tick() is False
            ensure.assert_not_called()
        finally:
            release()

    def test_loop_survives_a_failing_tick(self, monkeypatch):
        calls = []

        def failing_tick():
            calls.append(1)
            if len(calls) == 2:
                scheduler._stop.set()
            raise RuntimeError("nautobot down")

        monkeypatch.setattr(scheduler, "tick", failing_tick)
        monkeypatch.setattr(scheduler, "tick_seconds", lambda: 0)
        scheduler._stop.clear()
        try:
            scheduler.loop()
        finally:
            scheduler._stop.clear()
        assert len(calls) == 2

    def test_start_is_idempotent_and_respects_the_setting(self, monkeypatch):
        started = []
        monkeypatch.setattr(scheduler, "_started", False)
        monkeypatch.setattr(
            scheduler.threading, "Thread", lambda **kwargs: started.append(kwargs) or MagicMock()
        )
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", False)
        assert scheduler.start() is False
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", True)
        assert scheduler.start() is True
        assert scheduler.start() is True
        assert len(started) == 1
        assert started[0]["daemon"] is True

    def test_tick_seconds_follow_the_sooner_interval(self, monkeypatch):
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        assert scheduler.tick_seconds() == 30
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 10)
        assert scheduler.tick_seconds() == 10


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
    def _no_database(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")

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
            summary = alerts.get_alert_board_data()["summary"]
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


# ---------------------------------------------------------------------------
# Tests: alerts start when Nautobot's status changed, not when first seen (#166)
# ---------------------------------------------------------------------------
class TestRealStartTimes:
    CHECKED_AT = "2026-09-28T12:00:00+00:00"

    def test_down_since(self):
        now = self.CHECKED_AT
        # Nautobot status changed three days earlier -> that is when it went down.
        assert (
            alerts.down_since({"last_updated": "2026-09-25T12:00:00Z"}, now)
            == "2026-09-25T12:00:00+00:00"
        )
        # A later edit (clock skew, or an edit after we saw it) never moves the start later.
        assert alerts.down_since({"last_updated": "2026-09-28T13:00:00Z"}, now) == now
        # Unknown last change -> when we saw it.
        assert alerts.down_since({"last_updated": ""}, now) == now
        assert alerts.down_since({}, now) == now
        # Only LibreNMS says down: Nautobot's last_updated says nothing about this outage.
        assert (
            alerts.down_since(
                {"last_updated": "2026-09-25T12:00:00Z", "down_source": "librenms"}, now
            )
            == now
        )

    def test_librenms_only_down_is_marked(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "token")
        enriched = alerts.enrich_with_librenms(
            [
                {
                    "id": "d1",
                    "name": "sw01",
                    "status": "active",
                    "last_updated": "2026-01-01T00:00:00Z",
                },
                {
                    "id": "d2",
                    "name": "sw02",
                    "status": "offline",
                    "last_updated": "2026-01-01T00:00:00Z",
                },
            ],
            lnms_devices=[
                {"device_id": 1, "hostname": "sw01", "status": 0},
                {"device_id": 2, "hostname": "sw02", "status": 0},
            ],
            lnms_id_map={1: {}, 2: {}},
            snapshot_only=True,
        )
        assert enriched[0]["status"] == "offline" and enriched[0]["down_source"] == "librenms"
        # Already down in Nautobot: Nautobot is the source.
        assert "down_source" not in enriched[1]

    @pytest.fixture
    def board(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.CHECKED_AT)
        pg_database.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        pg_database.executemany(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip, last_updated) "
            "VALUES (%s, 'loc-1', %s, 'access', %s, %s, %s)",
            [
                ("d-old", "sw-old", "Offline", "10.0.0.1/32", "2026-09-25T12:00:00Z"),
                ("d-up", "sw-up", "Active", "10.0.0.2/32", "2026-09-01T00:00:00Z"),
                ("d-up2", "sw-up2", "Active", "10.0.0.3/32", "2026-09-01T00:00:00Z"),
                ("d-up3", "sw-up3", "Active", "10.0.0.4/32", "2026-09-01T00:00:00Z"),
            ],
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-28T11:00:00+00:00",
                last_completed_at="2026-09-28T11:00:00+00:00",
                last_successful_sync="2026-09-28T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

        def build():
            caching.cache.clear()
            return {row["id"]: row for row in alerts.get_alert_board_data()["alerts"]}

        return build

    def _start(self, pg_database):
        rows = pg_database.execute(
            "SELECT down_started_at FROM alert_instances WHERE device_id = 'd-old'"
        )
        return [db.serialize_value(row["down_started_at"]) for row in rows]

    def test_new_alert_starts_when_nautobot_status_changed(self, board, pg_database):
        site = board()["loc-1"]
        assert self._start(pg_database) == ["2026-09-25T12:00:00Z"]
        assert site["current_downtime_seconds"] == 3 * 24 * 3600

    def test_open_alert_is_moved_earlier_once_and_never_later(self, board, pg_database):
        # An alert opened before #166: started when the app first saw it.
        pg_database.execute(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES (%s, 'loc-1', 'Site One', 'd-old', 'sw-old', 'low', '', 'open', "
            "'2026-09-27T08:00:00Z', '2026-09-27T08:00:00Z', 0)",
            (db.build_alert_key("loc-1", "d-old"),),
        )
        board()
        assert self._start(pg_database) == ["2026-09-25T12:00:00Z"]
        # A later edit in Nautobot must not move it back.
        pg_database.execute(
            "UPDATE nautobot_device_cache SET last_updated = '2026-09-28T11:30:00Z'"
        )
        board()
        assert self._start(pg_database) == ["2026-09-25T12:00:00Z"]


# ---------------------------------------------------------------------------
# Tests: alert feed of devices going down and up and site severity changes (#180)
# ---------------------------------------------------------------------------
class TestAlertFeed:
    @pytest.fixture
    def board(self, pg_database, monkeypatch):
        """A site with two switches; returns (build, set_status, clock)."""
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        clock = {"now": "2026-09-28T12:00:00+00:00"}
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: clock["now"])
        pg_database.execute(
            "INSERT INTO nautobot_location_cache (location_id, name, location_type, status) "
            "VALUES ('loc-1', 'Site One', 'Office', 'Active')"
        )
        pg_database.executemany(
            "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, status, primary_ip) "
            "VALUES (%s, 'loc-1', %s, 'access', 'Active', %s)",
            [("d-1", "sw01", "10.0.0.1/32"), ("d-2", "sw02", "10.0.0.2/32")],
        )
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at="2026-09-28T11:00:00+00:00",
                last_completed_at="2026-09-28T11:00:00+00:00",
                last_successful_sync="2026-09-28T11:00:00+00:00",
                cache_version=inventory.CACHE_VERSION,
                status="idle",
            )
        conn.close()

        def build(at):
            clock["now"] = at
            caching.cache.clear()
            return {row["id"]: row for row in alerts.get_alert_board_data()["alerts"]}

        def set_status(device_id, status):
            pg_database.execute(
                "UPDATE nautobot_device_cache SET status = %s WHERE device_id = %s",
                (status, device_id),
            )

        return build, set_status

    def _feed(self, client, query=""):
        resp = client.get(f"/api/alert-feed{query}")
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    def test_level_changes_are_logged_but_not_first_sightings(self, pg_database):
        at = "2026-09-28T12:00:00+00:00"
        assert (
            alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "critical")}, at)
            == 0
        )
        assert (
            alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "critical")}, at)
            == 0
        )
        assert (
            alerts.record_site_level_changes({"s1": ("One", "low"), "s2": ("Two", "critical")}, at)
            == 1
        )
        rows = pg_database.execute("SELECT site_id, from_level, to_level FROM site_level_changes")
        assert rows == [{"site_id": "s1", "from_level": "ok", "to_level": "low"}]
        levels = pg_database.execute(
            "SELECT site_id, alert_level FROM site_alert_levels ORDER BY site_id"
        )
        assert levels == [
            {"site_id": "s1", "alert_level": "low"},
            {"site_id": "s2", "alert_level": "critical"},
        ]

    def test_failed_observation_is_not_a_level_change(self, board, monkeypatch, pg_database):
        build, _ = board
        build("2026-09-28T12:00:00+00:00")

        def fail(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(alerts, "get_location_devices_and_alert", fail)
        assert build("2026-09-28T12:05:00+00:00")["loc-1"]["alert_level"] == "no_data"
        assert pg_database.execute("SELECT count(*) AS n FROM site_level_changes") == [{"n": 0}]


# ---------------------------------------------------------------------------
# Tests: configurable geocoder, cache and rate limit (#197)
# ---------------------------------------------------------------------------
class TestGeocoder:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self, monkeypatch):
        caching.cache.clear()
        monkeypatch.setattr(inventory, "get_locations", lambda *a, **k: [])
        yield
        caching.cache.clear()

    def _geolocator(self, lat=55.68, lon=12.57):
        geolocator = MagicMock()
        geolocator.geocode.return_value = MagicMock(latitude=lat, longitude=lon)
        return geolocator


# ---------------------------------------------------------------------------
# Tests: browser security headers (#198)
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    pass  # its tests are held back (the end of the file says why)


# ---------------------------------------------------------------------------
# Tests: logging (#193)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: alert history retention (#194)
# ---------------------------------------------------------------------------
class TestHistoryRetention:
    def _alert(self, db_, device, status, resolved_days_ago=None):
        row = db_.execute(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "status, down_started_at, last_seen_down_at, resolved_at) VALUES "
            "(%s, 'loc-1', 'Site', %s, %s, 'low', %s, now() - interval '400 days', now() - interval '400 days', "
            "CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END) RETURNING id",
            (f"key-{device}", device, device, status, resolved_days_ago, resolved_days_ago),
        )[0]["id"]
        db_.execute(
            "INSERT INTO alert_events (alert_instance_id, event_type, event_at) VALUES (%s, 'opened', now())",
            (row,),
        )
        db_.execute(
            "INSERT INTO alert_cases (alert_instance_id, case_number) VALUES (%s, 'INC-1')", (row,)
        )
        return row

    def _change(self, db_, days_ago):
        db_.execute(
            "INSERT INTO site_level_changes (site_id, site_name, from_level, to_level, changed_at) "
            "VALUES ('loc-1', 'Site', 'ok', 'low', now() - make_interval(days => %s))",
            (days_ago,),
        )

    def _devices(self, db_):
        return sorted(r["device_id"] for r in db_.execute("SELECT device_id FROM alert_instances"))

    def test_deletes_only_old_resolved_history(self, pg_database):
        self._alert(pg_database, "old-resolved", "resolved", resolved_days_ago=100)
        self._alert(pg_database, "new-resolved", "resolved", resolved_days_ago=5)
        self._alert(pg_database, "old-open", "open")  # started 400 days ago, still down
        self._change(pg_database, 100)
        self._change(pg_database, 5)
        conn = db.get_conn()
        try:
            deleted = board_store.prune_alert_history(conn, 30)
        finally:
            conn.close()
        assert deleted == {"alert_instances": 1, "site_level_changes": 1}
        assert self._devices(pg_database) == ["new-resolved", "old-open"]
        # Its events and cases went with it.
        assert pg_database.execute("SELECT count(*) AS n FROM alert_events") == [{"n": 2}]
        assert pg_database.execute("SELECT count(*) AS n FROM alert_cases") == [{"n": 2}]

    def test_deletes_in_batches(self, pg_database, monkeypatch):
        monkeypatch.setattr(board_store, "RETENTION_BATCH_SIZE", 2)
        for i in range(5):
            self._alert(pg_database, f"d{i}", "resolved", resolved_days_ago=100)
        conn = db.get_conn()
        try:
            assert board_store.prune_alert_history(conn, 30)["alert_instances"] == 5
        finally:
            conn.close()
        assert self._devices(pg_database) == []

    def test_off_by_default_and_once_a_day(self, pg_database, monkeypatch):
        self._alert(pg_database, "old-resolved", "resolved", resolved_days_ago=100)
        conn = db.get_conn()
        try:
            monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 0)
            assert alerts.maybe_prune_alert_history(conn) is None
            assert self._devices(pg_database) == ["old-resolved"]

            monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 30)
            assert alerts.maybe_prune_alert_history(conn)["alert_instances"] == 1
            self._alert(pg_database, "later", "resolved", resolved_days_ago=100)
            assert alerts.maybe_prune_alert_history(conn) is None  # already ran today
            assert self._devices(pg_database) == ["later"]
        finally:
            conn.close()

    def test_scheduler_tick_runs_retention(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "ALERT_HISTORY_RETENTION_DAYS", 30)
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *a, **k: False)
        self._alert(pg_database, "old-resolved", "resolved", resolved_days_ago=100)
        scheduler.tick()
        assert self._devices(pg_database) == []


# ---------------------------------------------------------------------------
# Tests: Prometheus metrics and sync age (#200)
# ---------------------------------------------------------------------------
class TestMetrics:
    def _record(self, status, started, completed):
        conn = db.get_conn()
        with conn:
            inventory.record_sync_state(
                conn,
                "nautobot_inventory",
                last_started_at=started,
                last_completed_at=completed,
                last_successful_sync="2026-09-01T00:00:00+00:00",
                status=status,
            )
        conn.close()

    @staticmethod
    def _value(body: str, sample: str) -> float:
        line = next(line for line in body.splitlines() if line.startswith(sample + " "))
        return float(line.rsplit(" ", 1)[1])

    def test_label_values_are_escaped(self):
        from app.services import metrics_service as metrics

        out = metrics.Exposition()
        out.gauge("m", "h", [({"level": 'a"b\\c\nd'}, 1)])
        assert 'm{level="a\\"b\\\\c\\nd"} 1' in out.render()

    def test_timestamps_keep_full_precision(self):
        from app.services import metrics_service as metrics

        out = metrics.Exposition()
        out.gauge("t", "h", [({}, 1790676030.0), ({"x": "1"}, 0.25)])
        assert "t 1790676030\n" in out.render() and 't{x="1"} 0.25' in out.render()


# ---------------------------------------------------------------------------
# Tests: versioned schema migrations (#201)
# ---------------------------------------------------------------------------
# sha256 of db.baseline_schema's source.  The baseline is frozen: a schema
# change goes into a new step in db.MIGRATIONS, never into the baseline, or
# databases already at version 1 would never get it.
BASELINE_SCHEMA_SHA256 = "f5e812f54f7ffad33bf515db035d384fdb49f791a89451c3f623e12ca986178f"


# ---------------------------------------------------------------------------
# Tests: database connect and statement timeouts (#190)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tests: upstream HTTP retries and connection reuse (#192)
# ---------------------------------------------------------------------------


def _retry_kwargs(policy) -> dict:
    """The production retry settings, so tests only change the backoff."""
    return {
        "total": policy.total,
        "connect": policy.connect,
        "read": policy.read,
        "status": policy.status,
        "status_forcelist": policy.status_forcelist,
        "allowed_methods": policy.allowed_methods,
        "respect_retry_after_header": policy.respect_retry_after_header,
        "raise_on_status": policy.raise_on_status,
    }


# ---------------------------------------------------------------------------
# Tests: API explorer (#230)
# ---------------------------------------------------------------------------


def _snapshot_location(location_id: str, name: str, location_type: str = "Data Center") -> dict:
    return {
        "id": location_id,
        "name": name,
        "slug": location_id,
        "status": "Active",
        "location_type": location_type,
        "parent": "",
        "latitude": 55.0,
        "longitude": 12.0,
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


def _snapshot_device(device_id: str, location_id: str, role: str, status: str) -> dict:
    return {
        "id": device_id,
        "location_id": location_id,
        "name": device_id,
        "device_type": "",
        "manufacturer": "",
        "role": role,
        "status": status,
        "primary_ip": "10.0.0.1",
        "platform": "",
        "serial": "",
        "tenant": "",
        "last_updated": "2026-01-01T00:00:00Z",
    }


class TestLocationAlertLevels:
    """/api/location-alerts colours the map markers without a click (#234)."""

    @pytest.fixture(autouse=True)
    def _database(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
                    conn,
                    [
                        _snapshot_location("loc-core", "Core down"),
                        _snapshot_location("loc-few", "One of five down"),
                        _snapshot_location("loc-ok", "All up"),
                        _snapshot_location("loc-empty", "No devices"),
                    ],
                )
                inventory.write_devices(
                    conn,
                    [
                        _snapshot_device("core-1", "loc-core", "Core Router", "Offline"),
                        _snapshot_device("acc-1", "loc-core", "Access Switch", "Active"),
                        _snapshot_device("few-1", "loc-few", "Access Switch", "Offline"),
                        *[
                            _snapshot_device(f"few-{n}", "loc-few", "Access Switch", "Active")
                            for n in range(2, 6)
                        ],
                        _snapshot_device("ok-1", "loc-ok", "Access Switch", "Active"),
                    ],
                )
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:01:00Z",
                    last_successful_sync="2026-01-01T00:01:00Z",
                    status="success",
                    error_message="",
                )
        finally:
            conn.close()
        caching.cache.clear()


SAMPLE_CIRCUIT_TERMINATIONS = [
    {
        "id": "term-2",
        "circuit": {
            "id": "cir-2",
            "cid": "GTT-7781",
            "provider": {"id": "p2", "name": "GTT"},
            "circuit_type": {"id": "t2", "name": "MPLS"},
            "status": {"id": "s2", "name": "Offline"},
            "tenant": None,
            "commit_rate": 1000000,
        },
        "term_side": "Z",
        "port_speed": 1000000,
        "upstream_speed": 500000,
        "xconnect_id": "",
        "pp_info": "",
        "description": "",
    },
    {
        "id": "term-1",
        "circuit": {
            "id": "cir-1",
            "cid": "TEL-0001",
            "provider": {"id": "p1", "display": "Telia"},
            "circuit_type": {"id": "t1", "name": "Transit"},
            "status": {"id": "s1", "name": "Active"},
            "tenant": {"id": "ten-1", "name": "Acme Corp"},
            "commit_rate": None,
        },
        "term_side": "A",
        "port_speed": 10000000,
        "upstream_speed": None,
        "xconnect_id": "XC-1",
        "pp_info": "PP-02 port 7",
        "description": "Primary transit",
    },
]


class TestLocationCircuits:
    """The map's site panel lists every circuit at the location (#235)."""

    def _mock(self, terminations=None, error=None):
        def fetch(endpoint, params=None, **kwargs):
            if endpoint.startswith("circuits/"):
                if error is not None:
                    raise error
                assert params["location"] == "loc-1"
                assert params["depth"] == 2
                return terminations or []
            return [page for page in mock_nautobot_get(endpoint, params)["results"]]

        return patch.object(nautobot, "fetch_all_pages", side_effect=fetch)

    @pytest.mark.parametrize(
        ("kbps", "expected"),
        [
            (None, ""),
            (0, ""),
            ("bad", ""),
            (512, "512 Kbps"),
            (1500, "1.5 Mbps"),
            (100000, "100 Mbps"),
            (10000000, "10 Gbps"),
            (2500000, "2.5 Gbps"),
        ],
    )
    def test_circuit_speed(self, kbps, expected):
        assert inventory.circuit_speed(kbps) == expected


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
            raise requests.HTTPError(response=MagicMock(status_code=403))
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


class TestLibreNMSMappingsAreBatched:
    """Board builds write new LibreNMS mappings once, not a connection per device (#240)."""

    DEVICES = 60

    @pytest.fixture(autouse=True)
    def _inventory(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        # No background sync against the fake LibreNMS URL.
        monkeypatch.setattr(inventory, "ensure_snapshot", lambda *args, **kwargs: False)
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(conn, [_snapshot_location("loc-1", "Big site")])
                inventory.write_devices(
                    conn,
                    [
                        {
                            **_snapshot_device(f"dev-{n}", "loc-1", "Access Switch", "Active"),
                            "name": f"sw{n}",
                        }
                        for n in range(self.DEVICES)
                    ],
                )
                inventory.write_librenms_devices(
                    conn,
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
                inventory.record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:01:00Z",
                    last_successful_sync="2026-01-01T00:01:00Z",
                    status="success",
                    error_message="",
                )
        finally:
            conn.close()
        caching.cache.clear()

    def _mappings(self):
        conn = db.get_conn()
        try:
            return {
                row["nautobot_device_id"]: row["librenms_device_id"]
                for row in conn.execute("SELECT * FROM librenms_device_map").fetchall()
            }
        finally:
            conn.close()

    def _count_connections(self):
        counter = {"n": 0}
        real = db.get_conn

        def counting(*args, **kwargs):
            counter["n"] += 1
            return real(*args, **kwargs)

        return counter, patch.object(db, "get_conn", side_effect=counting)

    def test_board_build_uses_a_few_connections(self):
        counter, patched = self._count_connections()
        with (
            patched,
            patch.object(
                nautobot, "fetch_all_pages", side_effect=AssertionError("no upstream calls")
            ),
        ):
            board = alerts.get_alert_board_data()
        (site,) = board["alerts"]
        assert site["down_device_count"] == 1
        # Before #240 this was one connection per newly matched device.
        assert counter["n"] < 15, counter["n"]
        mappings = self._mappings()
        assert len(mappings) == self.DEVICES
        assert mappings["dev-7"] == 1007

    def test_map_levels_store_mappings_in_one_batch(self):
        # The map reads the board's rows (#316); the scheduler keeps it cached.
        alerts.cached_alert_board()
        counter, patched = self._count_connections()
        with patched:
            levels = alerts.build_location_alert_levels()["levels"]
        assert levels["loc-1"]["level"] == "low"
        assert counter["n"] < 10, counter["n"]
        assert len(self._mappings()) == self.DEVICES

    def test_persisted_mappings_win_and_are_not_rewritten(self):
        board_store.store_librenms_maps({"dev-1": (1000, "sw0.corp.example")})
        with patch.object(
            board_store, "store_librenms_maps", wraps=board_store.store_librenms_maps
        ) as store:
            board = alerts.get_alert_board_data()
        # dev-1 is mapped to LibreNMS device 1000, which is down.
        down = {d["device_id"] for d in board["alerts"][0]["down_devices"]}
        assert down == {"dev-0", "dev-1"}
        (written,) = [call.args[0] for call in store.call_args_list if call.args[0]]
        assert "dev-1" not in written and len(written) == self.DEVICES - 1

    def test_enrich_on_its_own_writes_once(self):
        devices = [{"id": f"dev-{n}", "name": f"sw{n}", "status": "Active"} for n in range(5)]
        with patch.object(board_store, "store_librenms_maps") as store:
            alerts.enrich_with_librenms(devices, snapshot_only=True)
        store.assert_called_once()
        assert set(store.call_args.args[0]) == {f"dev-{n}" for n in range(5)}

    def test_a_failed_write_does_not_fail_the_board(self, caplog):
        with patch.object(db, "transaction", side_effect=RuntimeError("db gone")):
            board_store.store_librenms_maps({"dev-1": (1, "sw1")})
        assert "Could not store 1 LibreNMS device mappings" in caplog.text


class TestAlertHistoryCsv:
    """A site's alert history as CSV (#277)."""

    NOW = "2026-10-07T12:00:00Z"

    @pytest.fixture(autouse=True)
    def _history(self, pg_database, monkeypatch):
        monkeypatch.setattr(timeutil, "iso_utc_now", lambda: self.NOW)
        conn = db.get_conn()
        try:
            with db.transaction(conn):
                for device_id, name, role, ip in (
                    ("d1", "lon-sw01", "Access Switch", "10.0.8.4/24"),
                    ("d2", "lon-core", "Core Router", ""),
                ):
                    conn.execute(
                        "INSERT INTO nautobot_device_cache (device_id, location_id, name, role, primary_ip) "
                        "VALUES (%s, 'loc-lon', %s, %s, %s)",
                        (device_id, name, role, ip),
                    )
                # d2 has no primary IP: the address LibreNMS polls is used.
                conn.execute("INSERT INTO librenms_device_map VALUES ('d2', 7, 'lon-core')")
                conn.execute(
                    "INSERT INTO librenms_device_status (device_id, hostname, ip) VALUES (7, 'lon-core', '10.9.9.9')"
                )
                # "gone" was deleted from Nautobot, but its LibreNMS mapping lingers.
                conn.execute("INSERT INTO librenms_device_map VALUES ('gone', 8, 'gone-host')")
                conn.execute(
                    "INSERT INTO librenms_device_status (device_id, hostname, ip) VALUES (8, 'gone-host', '10.6.6.6')"
                )
                incidents = [
                    # id, device, name, status, down, resolved, seconds, level
                    (
                        1,
                        "d1",
                        "lon-sw01",
                        "resolved",
                        "2026-10-01T08:12:00Z",
                        "2026-10-01T08:40:00Z",
                        1680,
                        "low",
                    ),
                    (2, "d1", "lon-sw01", "open", "2026-10-07T11:30:00Z", None, 0, "medium"),
                    (
                        3,
                        "d2",
                        "lon-core",
                        "resolved",
                        "2026-08-20T10:00:00Z",
                        "2026-09-27T10:00:00Z",
                        3283200,
                        "critical",
                    ),
                    (
                        4,
                        "d2",
                        "lon-core",
                        "resolved",
                        "2026-07-01T10:00:00Z",
                        "2026-07-01T11:00:00Z",
                        3600,
                        "critical",
                    ),
                    # Deleted from Nautobot since; a name that Excel would run as a formula.
                    (
                        5,
                        "gone",
                        "=HYPERLINK(1)",
                        "resolved",
                        "2026-10-05T09:00:00Z",
                        "2026-10-05T09:10:00Z",
                        600,
                        "low",
                    ),
                ]
                for row in incidents:
                    conn.execute(
                        "INSERT INTO alert_instances (id, alert_key, site_id, site_name, device_id, device_name, "
                        "status, down_started_at, last_seen_down_at, resolved_at, total_downtime_seconds, alert_level, "
                        "alert_reason) VALUES (%s, %s, 'loc-lon', 'London HQ', %s, %s, %s, %s, %s, %s, %s, %s, 'x')",
                        (
                            row[0],
                            f"k{row[0]}",
                            row[1],
                            row[2],
                            row[3],
                            row[4],
                            row[4],
                            row[5],
                            row[6],
                            row[7],
                        ),
                    )
                conn.execute(
                    "INSERT INTO alert_cases (alert_instance_id, case_number, created_by, created_at) VALUES "
                    "(1, 'INC-881', '', now()), (1, 'INC-1', '', now()), (2, 'INC-900', '', now())"
                )
        finally:
            conn.close()

    @staticmethod
    def _rows(resp):
        import csv as csv_module

        text = resp.get_data(as_text=True)
        assert text.startswith("﻿"), "BOM for Excel"
        return list(csv_module.DictReader(text[1:].splitlines()))


class TestAlertHistoryCsvCells:
    def test_formula_guard(self):
        from app.services import export_service as export

        assert [export.safe_cell(v) for v in ("=1+1", "+x", "-3", "@a", "ok", None, 5)] == [
            "'=1+1",
            "'+x",
            "'-3",
            "'@a",
            "ok",
            "",
            "5",
        ]

    def test_file_name_is_safe(self):
        from datetime import UTC, datetime

        from app.services import export_service as export

        now = datetime(2026, 10, 7, tzinfo=UTC)
        assert (
            export.file_name('København "DC" / 2', "devices", "90", now)
            == "k-benhavn-dc-2-devices-90d-2026-10-07.csv"
        )
        assert export.file_name("", "incidents", "all", now) == "site-incidents-all-2026-10-07.csv"


# Held back until what they need is ported (#587):
#   client: HTTP routes (step 7)
#   TestApiLocations.test_returns_locations_with_coordinates: HTTP routes (step 7)
#   TestApiLocations.test_location_fields: HTTP routes (step 7)
#   TestApiLocations.test_location_type_field_populated: HTTP routes (step 7)
#   TestApiLocations.test_country_field_populated: HTTP routes (step 7)
#   TestApiLocations.test_country_field_falls_back_to_physical_address: HTTP routes (step 7)
#   TestApiLocations.test_parent_field_populated: HTTP routes (step 7)
#   TestApiLocations.test_tenant_group_field_populated: HTTP routes (step 7)
#   TestApiLocations.test_tenant_group_empty_when_no_tenant: HTTP routes (step 7)
#   TestApiLocations.test_facility_field_populated: HTTP routes (step 7)
#   TestApiLocations.test_facility_empty_when_not_set: HTTP routes (step 7)
#   TestApiLocations.test_tags_field_populated: HTTP routes (step 7)
#   TestApiLocations.test_tags_empty_when_none: HTTP routes (step 7)
#   TestApiLocations.test_tags_fallback_with_brief_nested_object: HTTP routes (step 7)
#   TestApiLocations.test_location_type_fallback_with_brief_nested_object: HTTP routes (step 7)
#   TestApiLocations.test_parent_fallback_with_brief_nested_object: HTTP routes (step 7)
#   TestApiLocations.test_missing_env_vars_returns_503: HTTP routes (step 7)
#   TestApiLocations.test_nautobot_http_error_returns_502: HTTP routes (step 7)
#   TestAlertBoard.test_alert_board_page_renders: HTTP routes (step 7)
#   TestAlertBoard.test_api_alerts_returns_board_data: HTTP routes (step 7)
#   TestAlertBoard.test_api_alerts_hides_non_operational_locations_unless_requested: HTTP routes (step 7)
#   TestAlertBoard.test_log_alert_board_exclusions_reports_resolved_sets: the current app's app.py startup (step 7)
#   TestApiLocationDetail.test_returns_devices_and_asns: HTTP routes (step 7)
#   TestApiLocationDetail.test_device_fields: HTTP routes (step 7)
#   TestApiLocationDetail.test_device_with_null_fields: HTTP routes (step 7)
#   TestApiLocationDetail.test_device_lookup_failure_returns_503: HTTP routes (step 7)
#   TestApiLocationDetail.test_asn_lookup_http_error_returns_502: HTTP routes (step 7)
#   TestApiLocationDetail.test_missing_asn_endpoint_falls_back_to_location_asn: HTTP routes (step 7)
#   TestApiLocationDetail.test_missing_asn_endpoint_prefers_cached_location_asn: HTTP routes (step 7)
#   TestApiLocationDetail.test_missing_asn_endpoint_and_no_location_asn_returns_empty: HTTP routes (step 7)
#   TestApiLocationDetail.test_missing_asn_endpoint_still_fails_when_location_lookup_fails: HTTP routes (step 7)
#   TestApiSearch.test_missing_query_returns_400: HTTP routes (step 7)
#   TestApiSearch.test_gps_coordinates_search: HTTP routes (step 7)
#   TestApiSearch.test_gps_no_results_far_away: HTTP routes (step 7)
#   TestApiSearch.test_search_results_sorted_by_distance: HTTP routes (step 7)
#   TestApiSearch.test_address_geocoding: HTTP routes (step 7)
#   TestApiSearch.test_address_not_found_returns_404: HTTP routes (step 7)
#   TestApiSearch.test_distance_km_field_present: HTTP routes (step 7)
#   TestNautobotRuntimeErrors.test_runtime_errors_do_not_leak_internal_messages: HTTP routes (step 7)
#   TestIndex.test_index_is_the_nuxt_page: HTTP routes (step 7)
#   TestIndex.test_what_the_map_reads: HTTP routes (step 7)
#   TestSSLVerification.test_missing_ca_bundle_is_logged_at_startup: the current app's app.py startup (step 7)
#   TestErrorHandlers.test_404_html_for_browser: HTTP routes (step 7)
#   TestErrorHandlers.test_404_json_for_api_path: HTTP routes (step 7)
#   TestErrorHandlers.test_404_json_when_accept_json: HTTP routes (step 7)
#   TestErrorHandlers.test_405_html_for_browser: HTTP routes (step 7)
#   TestErrorHandlers.test_405_json_for_api_path: HTTP routes (step 7)
#   TestErrorHandlers.test_http_exception_returns_json_for_api_path: HTTP routes (step 7)
#   TestLocationDetailWithLocationType.test_location_type_param_accepted: HTTP routes (step 7)
#   TestLocationDetailWithLocationType.test_location_type_influences_alert: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_list_empty: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_create_override: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_list_after_create: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_update_override: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_delete_override: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_delete_nonexistent_returns_404: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_create_missing_device_id_returns_400: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_no_db_returns_503: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_requires_operator_role_when_auth_enabled: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_accepts_operator_group_when_auth_enabled: HTTP routes (step 7)
#   TestCriticalityOverrideEndpoints.test_uses_authenticated_username_as_updated_by: HTTP routes (step 7)
#   TestAlertLifecycleTracking.test_api_alerts_contains_lifecycle_fields: HTTP routes (step 7)
#   TestAlertLifecycleTracking.test_alert_history_tracks_open_and_resolve: HTTP routes (step 7)
#   TestAlertLifecycleTracking.test_add_case_number_to_active_alert: HTTP routes (step 7)
#   TestAlertLifecycleTracking.test_api_alerts_preserves_case_numbers_on_current_down_devices: HTTP routes (step 7)
#   TestAlertLifecycleTracking.test_add_case_uses_authenticated_user_for_created_by: HTTP routes (step 7)
#   TestAlertLifecycleTracking.test_alert_history_rejects_invalid_timestamp_filters: HTTP routes (step 7)
#   TestInventoryCacheSync.test_api_alerts_filters_cached_devices_without_primary_ip: HTTP routes (step 7)
#   TestAuthConfiguration.test_header_auth_binds_flask_to_loopback: the current app's statuslinks/tokens/auth modules (step 7)
#   TestAuthConfiguration.test_non_header_auth_keeps_public_flask_bind: the current app's statuslinks/tokens/auth modules (step 7)
#   TestAuthConfiguration.test_identity_headers_only_count_from_trusted_proxies: HTTP routes (step 7)
#   TestAuthConfiguration.test_proxy_secret_is_required_when_set: HTTP routes (step 7)
#   TestAuthConfiguration.test_auth_disabled_refuses_admin_writes_by_default: HTTP routes (step 7)
#   TestAuthConfiguration.test_auth_disabled_keeps_reads_and_cases_open: HTTP routes (step 7)
#   TestAuthConfiguration.test_nautobot_proxy_endpoints_are_gone: HTTP routes (step 7)
#   TestAuthConfiguration.test_allow_unauthenticated_writes_restores_the_old_behaviour: HTTP routes (step 7)
#   TestAuthConfiguration.test_require_viewer_protects_every_page_but_probes: HTTP routes (step 7)
#   TestAuthConfiguration.test_require_viewer_is_off_by_default: HTTP routes (step 7)
#   TestAuthConfiguration.test_missing_identity_header_returns_401: HTTP routes (step 7)
#   TestAuthConfiguration.test_default_role_allows_authenticated_viewer_only_access: HTTP routes (step 7)
#   TestAlertBoardTierDefinitions.test_the_board_explains_the_levels_and_chips: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_refresh_1_enqueues_forced_background_sync: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_timestamp_refresh_value_does_not_trigger_sync: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_cold_start_enqueues_first_sync_without_waiting: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_initialized_snapshot_does_not_start_sync_and_is_not_pending: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_due_sync_is_started_by_a_normal_board_load: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_next_update_counts_down_from_last_completed_sync: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_running_sync_is_reported_as_pending: HTTP routes (step 7)
#   TestAlertBoardSyncProgress.test_adding_case_invalidates_cached_board: HTTP routes (step 7)
#   TestMultiDeviceCases.test_links_one_case_to_every_selected_device: HTTP routes (step 7)
#   TestMultiDeviceCases.test_is_all_or_nothing_when_a_device_has_no_open_alert: HTTP routes (step 7)
#   TestMultiDeviceCases.test_duplicate_ids_are_linked_once: HTTP routes (step 7)
#   TestMultiDeviceCases.test_single_device_id_keeps_original_response_fields: HTTP routes (step 7)
#   TestMultiDeviceCases.test_rejects_invalid_device_ids: HTTP routes (step 7)
#   TestHealthz.test_ok_without_persistence: HTTP routes (step 7)
#   TestHealthz.test_ok_with_reachable_database: HTTP routes (step 7)
#   TestHealthz.test_unavailable_database_returns_503_without_details: HTTP routes (step 7)
#   TestHealthz.test_makes_no_upstream_calls: HTTP routes (step 7)
#   TestAlertBoardWithoutPersistence.test_payload_reports_missing_database: HTTP routes (step 7)
#   TestAlertBoardWithoutPersistence.test_payload_reports_configured_database: HTTP routes (step 7)
#   TestAlertBoardWithoutPersistence.test_startup_log_warns_without_database: the current app's app.py startup (step 7)
#   TestAlertBoardWithoutPersistence.test_startup_log_errors_on_leftover_sqlite_setting: the current app's app.py startup (step 7)
#   TestAlertBoardWithoutPersistence.test_startup_log_quiet_with_database: the current app's app.py startup (step 7)
#   TestAlertFeed.test_board_builds_fill_the_feed: HTTP routes (step 7)
#   TestAlertFeed.test_a_board_for_some_sites_reads_their_feed: HTTP routes (step 7)
#   TestAlertFeed.test_a_scoped_feed_without_stored_sites_says_so: HTTP routes (step 7)
#   TestAlertFeed.test_bad_parameters_are_rejected: HTTP routes (step 7)
#   TestAlertFeed.test_without_persistence_the_feed_is_empty: HTTP routes (step 7)
#   TestGeocoder.test_disabled_geocoder_only_accepts_coordinates: HTTP routes (step 7)
#   TestGeocoder.test_uses_the_configured_service: HTTP routes (step 7)
#   TestGeocoder.test_results_are_cached: HTTP routes (step 7)
#   TestGeocoder.test_at_most_one_request_per_second: HTTP routes (step 7)
#   TestGeocoder.test_the_interval_is_one_second: the routes' answer models (step 7)
#   TestGeocoder.test_geocoder_error_is_not_passed_to_the_client: HTTP routes (step 7)
#   TestGeocoder.test_not_found_is_cached_too: HTTP routes (step 7)
#   TestSecurityHeaders.test_headers_on_every_response: HTTP routes (step 7)
#   TestSecurityHeaders.test_images_allowed_from_the_tile_server: HTTP routes (step 7)
#   TestLogging.test_unexpected_error_is_logged_with_its_stack_trace: HTTP routes (step 7)
#   TestMetrics.test_last_success_survives_a_failure: HTTP routes (step 7)
#   TestMetrics.test_alert_and_site_counts: HTTP routes (step 7)
#   TestMetrics.test_without_database: HTTP routes (step 7)
#   TestMetrics.test_can_be_turned_off: HTTP routes (step 7)
#   TestMetrics.test_healthz_reports_sync_age_without_failing: HTTP routes (step 7)
#   TestDatabaseTimeouts.test_healthz_uses_a_short_connect_timeout: HTTP routes (step 7)
#   TestApiExplorer.test_every_route_is_listed: HTTP routes (step 7)
#   TestApiExplorer.test_what_each_endpoint_takes: HTTP routes (step 7)
#   TestApiExplorer.test_the_page_is_the_nuxt_page: HTTP routes (step 7)
#   TestApiExplorer.test_write_bodies_are_in_the_spec: HTTP routes (step 7)
#   TestApiExplorer.test_the_menu_links_to_the_explorer: HTTP routes (step 7)
#   TestApiExplorer.test_the_old_paths_move_for_good: HTTP routes (step 7)
#   TestApiExplorer.test_the_reference_is_a_page_not_json: HTTP routes (step 7)
#   TestApiExplorer.test_needs_the_viewer_role_like_every_page: HTTP routes (step 7)
#   TestLocationAlertLevels.test_returns_every_location_with_monitored_devices: HTTP routes (step 7)
#   TestLocationAlertLevels.test_matches_the_site_panel: HTTP routes (step 7)
#   TestLocationAlertLevels.test_the_site_panel_marks_core_devices: HTTP routes (step 7)
#   TestLocationAlertLevels.test_criticality_override_is_applied: HTTP routes (step 7)
#   TestLocationAlertLevels.test_cached_until_a_sync_invalidates_it: HTTP routes (step 7)
#   TestLocationCircuits.test_every_circuit_is_listed: HTTP routes (step 7)
#   TestLocationCircuits.test_no_circuits_app_is_not_an_error: HTTP routes (step 7)
#   TestLocationCircuits.test_a_failed_call_keeps_the_rest_of_the_panel: HTTP routes (step 7)
#   TestLocationTenantSync.test_sync_fills_the_board_and_the_map: HTTP routes (step 7)
#   TestLocationTenantSync.test_a_failed_read_keeps_the_links_a_removed_one_goes: HTTP routes (step 7)
#   TestTenantDescriptions.test_board_and_map_get_the_descriptions_of_their_tenants: HTTP routes (step 7)
#   TestTenantDescriptions.test_an_edited_description_replaces_the_old_one: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_incidents_in_the_last_30_days: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_incidents_are_streamed_in_batches: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_all_and_seven_days: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_per_device: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_history_panel_is_newest_down_first: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_bad_requests: HTTP routes (step 7)
#   TestAlertHistoryCsv.test_needs_the_operator_role: HTTP routes (step 7)

# The current app's own schema migration and gunicorn tests are its own: the schema
# here is Alembic's (tests/test_schema.py), the server uvicorn:
#   TestAlertLifecycleTracking.test_init_db_rekeys_open_alerts_without_severity
#   TestAlertLifecycleTracking.test_app_starts_with_open_alerts_to_migrate
#   TestAlertLifecycleTracking.test_init_db_postgres_uses_advisory_lock_before_ddl
#   TestAlertLifecycleTracking.test_init_db_postgres_marks_primary_ip_migration_pending
#   TestAlertLifecycleTracking.test_init_db_postgres_migrates_legacy_sync_state_cache_version
#   TestAlertLifecycleTracking.test_init_db_migrates_legacy_time_zone_not_null
#   TestAlertLifecycleTracking.test_init_db_marks_primary_ip_migration_pending
#   TestAuthConfiguration.test_header_auth_binds_gunicorn_to_all_interfaces
#   TestAuthConfiguration.test_non_header_auth_keeps_public_gunicorn_bind
#   TestAuthConfiguration.test_gunicorn_when_ready_logs_alert_board_exclusions
#   TestAuthConfiguration.test_default_gunicorn_timeout_is_120_seconds
#   TestAuthConfiguration.test_gunicorn_timeout_has_a_120_second_floor
#   TestAuthConfiguration.test_gunicorn_timeout_allows_values_above_floor
#   TestLibreNMSPolledIp.test_migration_adds_ip_column_to_existing_table
#   TestLibreNMSPolledIp.test_postgres_migration_adds_ip_column
#   TestSiteRollup.test_migration_adds_parent_id_column
#   TestBackgroundScheduler.test_gunicorn_starts_the_scheduler_in_each_worker
#   TestMetrics.test_migration_backfills_last_success

# Left out, with why:
#   TestAlertBoard.test_get_alert_board_data_replaces_write_connection_after_failure: counts connections with no database configured; the new stack always has its database
#   TestAlertBoard.test_get_alert_board_data_disables_persistence_after_connect_failure: no database configured (db.dialect() ""); the new stack always has its database
#   TestAlertLifecycleTracking.test_get_db_conn_postgres_enables_autocommit: the current app's psycopg.connect; app/db.py's connections: tests/test_db.py
#   TestAlertBoardSyncProgress.test_empty_sync_interval_env_uses_default: a subprocess of the current app's settings; empty-means-default is tests/test_settings.py
#   TestBackgroundScheduler.test_not_started_without_database: no database configured; the new stack always has its database
#   TestApiExplorer.test_every_write_has_an_example_body: the API explorer (apidocs), step 9 of #587
#   TestApiExplorer (the whole class): the API explorer (apidocs), step 9 of #587
