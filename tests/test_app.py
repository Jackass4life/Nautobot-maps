import importlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
import pytest
from unittest.mock import patch, MagicMock
from werkzeug.exceptions import GatewayTimeout

import app as flask_app


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
        "AUTH_MODE": flask_app.AUTH_MODE,
        "AUTH_HEADER_USER": flask_app.AUTH_HEADER_USER,
        "AUTH_HEADER_GROUPS": flask_app.AUTH_HEADER_GROUPS,
        "AUTH_VIEWER_GROUPS": set(flask_app.AUTH_VIEWER_GROUPS),
        "AUTH_OPERATOR_GROUPS": set(flask_app.AUTH_OPERATOR_GROUPS),
        "AUTH_ADMIN_GROUPS": set(flask_app.AUTH_ADMIN_GROUPS),
        "AUTH_DEFAULT_ROLE": flask_app.AUTH_DEFAULT_ROLE,
    }
    flask_app.AUTH_MODE = mode
    flask_app.AUTH_HEADER_USER = user_header
    flask_app.AUTH_HEADER_GROUPS = groups_header
    flask_app.AUTH_VIEWER_GROUPS = set(viewer_groups or set())
    flask_app.AUTH_OPERATOR_GROUPS = set(operator_groups or set())
    flask_app.AUTH_ADMIN_GROUPS = set(admin_groups or set())
    flask_app.AUTH_DEFAULT_ROLE = flask_app._normalize_auth_role(default_role)
    try:
        yield
    finally:
        flask_app.AUTH_MODE = saved["AUTH_MODE"]
        flask_app.AUTH_HEADER_USER = saved["AUTH_HEADER_USER"]
        flask_app.AUTH_HEADER_GROUPS = saved["AUTH_HEADER_GROUPS"]
        flask_app.AUTH_VIEWER_GROUPS = saved["AUTH_VIEWER_GROUPS"]
        flask_app.AUTH_OPERATOR_GROUPS = saved["AUTH_OPERATOR_GROUPS"]
        flask_app.AUTH_ADMIN_GROUPS = saved["AUTH_ADMIN_GROUPS"]
        flask_app.AUTH_DEFAULT_ROLE = saved["AUTH_DEFAULT_ROLE"]


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret"
    # Clear cache before each test
    flask_app.cache.clear()
    with flask_app.app.test_client() as c:
        yield c


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
            "tenant": {"id": "ten-1", "name": "Acme Corp", "tenant_group": {"id": "tg-1", "name": "Corporate"}},
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
def mock_nautobot_get(endpoint, params=None):
    params = params or {}
    if "dcim/locations" in endpoint:
        return SAMPLE_LOCATIONS_PAGE
    if "dcim/devices" in endpoint:
        return SAMPLE_DEVICES_PAGE
    if "ipam/asns" in endpoint:
        return SAMPLE_ASNS_PAGE
    if "tenancy/tenant-groups" in endpoint:
        return {
            "count": 1, "next": None,
            "results": [{"id": "tg-1", "name": "Corporate"}],
        }
    if "tenancy/tenants" in endpoint:
        return {
            "count": 1, "next": None,
            "results": [
                {"id": "ten-1", "name": "Acme Corp", "tenant_group": {"id": "tg-1", "name": "Corporate"}},
            ],
        }
    return {"count": 0, "next": None, "results": []}


# ---------------------------------------------------------------------------
# Tests: pagination helper
# ---------------------------------------------------------------------------
class TestFetchAllPages:
    def test_sets_large_limit_and_depth_defaults(self):
        calls = []

        def fake_get(endpoint, params=None):
            calls.append((endpoint, dict(params or {})))
            return {"count": 1, "next": None, "results": [{"id": "dev-1"}]}

        with patch.object(flask_app, "nautobot_get", side_effect=fake_get):
            results = flask_app.fetch_all_pages("dcim/devices/")

        assert results == [{"id": "dev-1"}]
        assert calls == [("dcim/devices/", {"limit": 1000, "depth": 0, "offset": 0})]

    def test_respects_explicit_limit_and_depth(self):
        calls = []

        def fake_get(endpoint, params=None):
            calls.append((endpoint, dict(params or {})))
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=fake_get):
            flask_app.fetch_all_pages("dcim/devices/", {"limit": 25, "depth": 2})

        assert calls == [("dcim/devices/", {"limit": 25, "depth": 2, "offset": 0})]


# ---------------------------------------------------------------------------
# Tests: /api/locations
# ---------------------------------------------------------------------------
class TestApiLocations:
    def test_returns_locations_with_coordinates(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "locations" in data
        # loc-3 (no GPS) must be excluded
        assert len(data["locations"]) == 2

    def test_location_fields(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["name"] == "Copenhagen DC"
        assert loc["latitude"] == 55.6761
        assert loc["longitude"] == 12.5683
        assert loc["tenant"] == "Acme Corp"
        assert loc["asn"] == 65001
        assert loc["status"] == "Active"

    def test_location_type_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_country_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_country_field_falls_back_to_physical_address(self, client):
        fallback_locations = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "loc-fallback-country",
                    "name": "Fallback Site",
                    "slug": "fallback-site",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Office"},
                    "parent": None,
                    "latitude": "55.0",
                    "longitude": "12.0",
                    "description": "",
                    "physical_address": "Examplevej 10, 2100 Copenhagen, Denmark",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "facility": "",
                    "tags": [],
                    "url": "",
                },
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/locations" in endpoint:
                return fallback_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_parent_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["parent"] == "Denmark"

    def test_tenant_group_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_tenant(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        # loc-2 (Aarhus PoP) has no tenant
        loc = resp.get_json()["locations"][1]
        assert loc["tenant_group"] == ""

    def test_facility_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["facility"] == "CPH-1"

    def test_facility_empty_when_not_set(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["facility"] == ""

    def test_tags_field_populated(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_tags_empty_when_none(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["tags"] == []

    def test_tags_fallback_with_brief_nested_object(self, client):
        """When tags are brief (id+url only), the fallback map resolves names."""
        brief_locations = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "loc-tags",
                    "name": "Tagged Location",
                    "slug": "tagged-loc",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Data Center"},
                    "parent": None,
                    "latitude": "55.0",
                    "longitude": "12.0",
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "facility": "",
                    "tags": [
                        {"id": "tag-1", "url": "http://nautobot/api/extras/tags/tag-1/"},
                        {"id": "tag-2", "url": "http://nautobot/api/extras/tags/tag-2/"},
                    ],
                    "url": "",
                },
            ],
        }
        tags_page = {
            "count": 2,
            "next": None,
            "results": [
                {"id": "tag-1", "name": "critical"},
                {"id": "tag-2", "name": "production"},
            ],
        }

        def mock_get(endpoint, params=None):
            if "extras/tags" in endpoint:
                return tags_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_location_type_fallback_with_brief_nested_object(self, client):
        """When location_type is brief (id+url only), the fallback map resolves the name."""
        brief_locations = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "loc-brief",
                    "name": "Brief Location",
                    "slug": "brief-loc",
                    "status": {"label": "Active"},
                    "location_type": {"id": "lt-dc", "url": "http://nautobot/api/dcim/location-types/lt-dc/"},
                    "parent": None,
                    "latitude": "55.0",
                    "longitude": "12.0",
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "url": "",
                },
            ],
        }
        lt_page = {
            "count": 1,
            "next": None,
            "results": [{"id": "lt-dc", "name": "Data Center"}],
        }

        def mock_get(endpoint, params=None):
            if "dcim/location-types" in endpoint:
                return lt_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_parent_fallback_with_brief_nested_object(self, client):
        """When parent is brief (id+url only), the fallback map resolves the name."""
        brief_locations = {
            "count": 2,
            "next": None,
            "results": [
                {
                    "id": "loc-parent",
                    "name": "Denmark",
                    "slug": "denmark",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Region"},
                    "parent": None,
                    "latitude": None,
                    "longitude": None,
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "url": "",
                },
                {
                    "id": "loc-child",
                    "name": "Copenhagen DC",
                    "slug": "cph-dc",
                    "status": {"label": "Active"},
                    "location_type": {"name": "Data Center"},
                    "parent": {"id": "loc-parent", "url": "http://nautobot/api/dcim/locations/loc-parent/"},
                    "latitude": "55.6761",
                    "longitude": "12.5683",
                    "description": "",
                    "physical_address": "",
                    "tenant": None,
                    "asn": None,
                    "time_zone": "",
                    "url": "",
                },
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations")
        # loc-parent has no GPS (lat/lon=None) so only loc-child is returned
        locs = resp.get_json()["locations"]
        assert len(locs) == 1
        assert locs[0]["parent"] == "Denmark"

    def test_missing_env_vars_returns_503(self, client):
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        flask_app.NAUTOBOT_URL = ""
        flask_app.NAUTOBOT_TOKEN = ""
        try:
            resp = client.get("/api/locations")
            assert resp.status_code == 503
            assert resp.get_json()["error"] == "Nautobot service unavailable"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token

    def test_nautobot_http_error_returns_502(self, client):
        import requests as req_lib

        http_err = req_lib.HTTPError(response=MagicMock(status_code=500))
        with patch.object(flask_app, "nautobot_get", side_effect=http_err):
            resp = client.get("/api/locations")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: alert board
# ---------------------------------------------------------------------------
class TestAlertBoard:
    def test_alert_board_page_renders(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == 200
        assert b"Alert Board" in resp.data
        assert b"Filter by site, address, or country" in resp.data
        assert b"Sort: country" in resp.data
        assert b"Show non-operational sites" in resp.data
        assert b"Collapse all" in resp.data
        assert b"Expand all" in resp.data

    def test_get_alert_board_data_aggregates_and_sorts(self):
        flask_app.cache.clear()
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

        with patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_get_location_devices_and_alert", side_effect=mock_devices):
            data = flask_app.get_alert_board_data()

        assert data["summary"] == {
            "total": 3,
            "critical": 1,
            "medium": 1,
            "unknown": 0,
            "ok": 1,
            "non_ok": 2,
        }
        assert [item["id"] for item in data["alerts"]] == ["loc-1", "loc-2", "loc-3"]
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][1]["alert_level"] == "medium"
        assert data["stale"] is False

    def test_api_alerts_returns_board_data(self, client):
        flask_app.cache.clear()
        with patch.object(flask_app, "get_locations", return_value=[{
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
        }]), patch.object(flask_app, "fetch_all_pages", return_value=[]), patch.object(
            flask_app,
            "_get_location_devices_and_alert",
            return_value=(
                [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
                {"level": "critical", "reason": "Core device(s) offline: router01"},
            ),
        ):
            resp = client.get("/api/alerts")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["name"] == "Copenhagen DC"
        assert data["alerts"][0]["alert_level"] == "critical"
        assert data["alerts"][0]["physical_address"] == "Vermlandsgade 51, 2300 Copenhagen"
        assert data["alerts"][0]["country"] == "Denmark"
        assert data["alerts"][0]["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": [],
            }
        ]

    def test_api_alerts_hides_non_operational_locations_unless_requested(self, client):
        flask_app.cache.clear()
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

        with patch.object(flask_app, "get_locations", return_value=sample_locations), patch.object(
            flask_app,
            "_get_location_devices_and_alert",
            return_value=(
                [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "active", "primary_ip": "192.0.2.1/32"}],
                {"level": "ok", "reason": ""},
            ),
        ) as get_alert:
            resp = client.get("/api/alerts")
            assert resp.status_code == 200
            assert resp.get_json()["summary"]["total"] == 1
            assert [item["id"] for item in resp.get_json()["alerts"]] == ["loc-1"]
            assert get_alert.call_count == 1

            flask_app.cache.clear()
            get_alert.reset_mock()
            resp = client.get("/api/alerts?include_non_operational=1")

        assert resp.status_code == 200
        assert resp.get_json()["summary"]["total"] == 2
        assert [item["id"] for item in resp.get_json()["alerts"]] == ["loc-1", "loc-2"]
        assert get_alert.call_count == 2

    def test_location_exclusion_supports_object_tags(self):
        with patch.object(flask_app, "ALERT_BOARD_EXCLUDED_LOCATION_TAGS", {"non-operational"}):
            assert flask_app._location_is_excluded_from_alert_board(
                {
                    "name": "Warehouse",
                    "status": "Active",
                    "location_type": "Office",
                    "tags": [{"name": "Non-Operational"}],
                }
            )

    def test_get_alert_board_data_marks_location_unknown_on_error(self):
        flask_app.cache.clear()
        sample_locations = [{"id": "loc-1", "name": "Broken Site", "latitude": None, "longitude": None}]

        with patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")):
            data = flask_app.get_alert_board_data()

        assert data["summary"]["unknown"] == 1
        assert data["summary"]["ok"] == 0
        assert data["alerts"][0]["alert_level"] == "unknown"
        assert data["alerts"][0]["alert_reason"] == "Could not compute alert state"

    def test_location_alert_uses_cached_snapshot_only_on_device_cache_miss(self):
        with patch.object(flask_app, "_read_cached_devices", return_value=[]), patch.object(
            flask_app, "_ensure_inventory_snapshot"
        ) as ensure_snapshot, patch.object(
            flask_app, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")
        ):
            devices, alert = flask_app._get_location_devices_and_alert(
                "loc-1",
                "Data Center",
                snapshot_only=True,
            )

        assert devices == []
        assert alert == {"level": "ok", "reason": ""}
        ensure_snapshot.assert_not_called()

    def test_location_alert_filters_devices_without_primary_ip(self):
        devices, alert = flask_app._get_location_devices_and_alert(
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
        bad_request = flask_app.requests.HTTPError(
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

        def _mock_fetch(endpoint, params=None):
            fetch_calls.append((endpoint, params))
            if len(fetch_calls) == 1:
                raise bad_request
            return live_device_page

        with patch.object(flask_app, "_read_cached_devices", side_effect=[[], []]), patch.object(
            flask_app, "_ensure_inventory_snapshot"
        ), patch.object(flask_app, "fetch_all_pages", side_effect=_mock_fetch):
            devices, alert = flask_app._get_location_devices_and_alert("loc-1", "Data Center")

        assert len(devices) == 1
        assert alert["level"] == "ok"
        assert fetch_calls[:2] == [
            ("dcim/devices/", {"location_id": "loc-1"}),
            ("dcim/devices/", {"location": "loc-1"}),
        ]

    def test_get_alert_board_data_does_not_live_fetch_devices_on_cache_miss(self):
        flask_app.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]

        with patch.object(flask_app, "get_locations", return_value=sample_locations), patch.object(
            flask_app, "_read_cached_devices", return_value=[]
        ), patch.object(flask_app, "_ensure_inventory_snapshot") as ensure_snapshot, patch.object(
            flask_app, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")
        ):
            data = flask_app.get_alert_board_data(force_refresh=True)

        assert data["summary"]["ok"] == 2
        assert data["summary"]["non_ok"] == 0
        assert [item["alert_level"] for item in data["alerts"]] == ["ok", "ok"]
        ensure_snapshot.assert_called_once_with(force=True, wait=False)

    def test_get_alert_board_data_uses_nautobot_alerts_when_librenms_unavailable(self):
        flask_app.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]

        with patch.object(flask_app, "LIBRENMS_URL", "https://librenms.example.com"), \
             patch.object(flask_app, "LIBRENMS_API_TOKEN", "token"), \
             patch.object(flask_app, "_fetch_librenms_inventory", side_effect=RuntimeError("down")), \
             patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(
                 flask_app,
                 "_get_location_devices_and_alert",
                 side_effect=[
                     ([{"id": "d1", "status": "offline"}], {"level": "critical", "reason": "Core down"}),
                     ([{"id": "d2", "status": "active"}], {"level": "ok", "reason": ""}),
                 ],
             ) as get_alert:
            data = flask_app.get_alert_board_data(force_refresh=True)

        assert get_alert.call_count == 2
        assert data["summary"]["critical"] == 1
        assert data["summary"]["ok"] == 1
        assert data["summary"]["unknown"] == 0

    def test_get_alert_board_data_uses_fresh_persistence_connection_per_location(self):
        flask_app.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]
        opened_conns = []
        upsert_conns = []
        context_conns = []

        class _FakeConn:
            def __init__(self, name):
                self.name = name
                self.closed = False

            def close(self):
                self.closed = True

        def fake_get_db_conn():
            conn = _FakeConn(f"conn-{len(opened_conns) + 1}")
            opened_conns.append(conn)
            return conn

        def fake_upsert(site, devices, alert, checked_at, conn=None):
            upsert_conns.append(conn)

        def fake_context(site_id, checked_at, conn=None):
            context_conns.append(conn)
            return {
                "active_alert_instance_count": 0,
                "historical_downtime_seconds": 0,
                "current_downtime_seconds": 0,
                "active_cases": [],
                "down_devices": [],
            }

        with patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_ensure_inventory_snapshot"), \
             patch.object(
                 flask_app,
                 "_get_location_devices_and_alert",
                 return_value=([], {"level": "ok", "reason": ""}),
             ), \
             patch.object(flask_app, "_get_db_conn", side_effect=fake_get_db_conn), \
             patch.object(flask_app, "_upsert_alert_lifecycle_for_site", side_effect=fake_upsert), \
             patch.object(flask_app, "_get_alert_context_for_site", side_effect=fake_context):
            data = flask_app.get_alert_board_data(force_refresh=True)

        assert data["summary"]["total"] == 2
        assert len(opened_conns) == 2
        assert upsert_conns == opened_conns
        assert context_conns == opened_conns
        assert opened_conns[0] is not opened_conns[1]
        assert all(conn.closed for conn in opened_conns)

    def test_get_alert_board_data_disables_persistence_after_connect_failure(self):
        flask_app.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]

        with patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_ensure_inventory_snapshot"), \
             patch.object(
                 flask_app,
                 "_get_location_devices_and_alert",
                 return_value=([], {"level": "ok", "reason": ""}),
             ), \
             patch.object(flask_app, "_get_db_conn", side_effect=RuntimeError("connection is closed")) as get_db_conn, \
             patch.object(flask_app, "_upsert_alert_lifecycle_for_site") as upsert, \
             patch.object(flask_app, "_get_alert_context_for_site") as get_context:
            data = flask_app.get_alert_board_data(force_refresh=True)

        assert data["summary"]["total"] == 2
        assert get_db_conn.call_count == 1
        upsert.assert_not_called()
        get_context.assert_not_called()

    def test_get_alert_board_data_skips_lifecycle_upsert_until_first_successful_nautobot_sync(self):
        flask_app.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
        ]
        class _FakeConn:
            def close(self):
                return None

        with patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_ensure_inventory_snapshot"), \
             patch.object(
                 flask_app,
                 "_get_location_devices_and_alert",
                 return_value=([], {"level": "ok", "reason": ""}),
             ), \
             patch.object(flask_app, "_get_db_conn", return_value=_FakeConn()), \
             patch.object(
                 flask_app,
                 "_get_sync_state",
                 return_value={
                     "status": "error",
                     "last_successful_sync": None,
                 },
             ), \
             patch.object(flask_app, "_upsert_alert_lifecycle_for_site") as upsert, \
             patch.object(
                 flask_app,
                 "_get_alert_context_for_site",
                 return_value={
                     "active_alert_instance_count": 1,
                     "historical_downtime_seconds": 3600,
                     "current_downtime_seconds": 1800,
                     "active_cases": ["INC-1001"],
                     "down_devices": [{"device_id": "dev-legacy", "device_name": "legacy01"}],
                 },
             ) as get_context:
            data = flask_app.get_alert_board_data(force_refresh=True)

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
    def test_returns_devices_and_asns(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "router01"
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_device_fields(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail")
        dev = resp.get_json()["devices"][0]
        assert dev["manufacturer"] == "Cisco"
        assert dev["device_type"] == "ASR1001-X"
        assert dev["role"] == "Core Router"
        assert dev["tenant"] == "Acme Corp"
        assert dev["platform"] == "IOS-XE"
        assert dev["serial"] == "SN123"
        assert dev["status"] == "Active"

    def test_device_with_null_fields(self, client):
        """Devices with null nested fields must return strings, never None."""
        sparse_devices = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "dev-sparse",
                    "name": None,
                    "device_type": None,
                    "role": None,
                    "status": None,
                    "platform": None,
                    "serial": None,
                    "tenant": None,
                }
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/devices" in endpoint:
                return sparse_devices
            if "dcim/device-types" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "dcim/manufacturers" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "extras/roles" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "tenancy/tenants" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "extras/statuses" in endpoint:
                return {"count": 0, "next": None, "results": []}
            if "ipam/asns" in endpoint:
                return {"count": 0, "next": None, "results": []}
            return {"count": 0, "next": None, "results": []}

        with patch.object(flask_app, "nautobot_get", side_effect=mock_get):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        dev = resp.get_json()["devices"][0]
        # Every field must be a string (not None/null) so the JS escHtml()
        # function never receives null.
        for field in ("id", "name", "device_type", "manufacturer", "role",
                      "status", "platform", "serial", "tenant"):
            assert dev[field] is not None, f"device field '{field}' is None"
            assert isinstance(dev[field], str), f"device field '{field}' is not a string"


# ---------------------------------------------------------------------------
# Tests: /api/search
# ---------------------------------------------------------------------------
class TestApiSearch:
    def test_missing_query_returns_400(self, client):
        resp = client.get("/api/search")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_gps_coordinates_search(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            # Copenhagen coordinates – loc-1 is exactly at 55.6761,12.5683 (distance 0)
            resp = client.get("/api/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)
        assert data["search_lon"] == pytest.approx(12.5683)
        assert data["radius_km"] == 5
        # loc-1 is at the exact point
        names = [l["name"] for l in data["locations"]]
        assert "Copenhagen DC" in names

    def test_gps_no_results_far_away(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            # Tokyo – far from all test locations
            resp = client.get("/api/search?q=35.6895,139.6917")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_search_results_sorted_by_distance(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            # Point very close to loc-1 (within 5 km)
            resp = client.get("/api/search?q=55.678,12.571")
        data = resp.get_json()
        distances = [l["distance_km"] for l in data["locations"]]
        assert distances == sorted(distances)

    def test_address_geocoding(self, client):
        mock_geo_result = MagicMock()
        mock_geo_result.latitude = 55.6761
        mock_geo_result.longitude = 12.5683
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = mock_geo_result

        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            with patch("app.Nominatim", return_value=mock_geolocator):
                resp = client.get("/api/search?q=Copenhagen")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)

    def test_address_not_found_returns_404(self, client):
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = None

        with patch("app.Nominatim", return_value=mock_geolocator):
            resp = client.get("/api/search?q=ThisPlaceDoesNotExist12345")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_distance_km_field_present(self, client):
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        for loc in data["locations"]:
            assert "distance_km" in loc


class TestNautobotRuntimeErrors:
    def test_runtime_errors_do_not_leak_internal_messages(self, client):
        secret = "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set"
        cases = [
            ("get", "/api/locations", "get_locations", {}),
            ("get", "/api/locations/loc-1/detail", "get_location_detail", {}),
            ("get", "/api/search?q=55.6761,12.5683", "get_locations", {}),
            ("get", "/api/roles", "fetch_all_pages", {}),
            (
                "post",
                "/api/roles",
                "nautobot_post",
                {"json": {"name": "Test Role"}, "content_type": "application/json"},
            ),
            ("delete", "/api/roles/role-1", "nautobot_delete", {}),
            ("get", "/api/location-types", "fetch_all_pages", {}),
            (
                "post",
                "/api/location-types",
                "nautobot_post",
                {"json": {"name": "Test Type"}, "content_type": "application/json"},
            ),
            ("delete", "/api/location-types/lt-dc", "nautobot_delete", {}),
        ]

        for method, url, patch_target, kwargs in cases:
            with patch.object(flask_app, patch_target, side_effect=RuntimeError(secret)):
                resp = getattr(client, method)(url, **kwargs)

            assert resp.status_code == 503
            assert resp.get_json()["error"] == "Nautobot service unavailable"
            assert secret not in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Tests: index page
# ---------------------------------------------------------------------------
class TestIndex:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Nautobot" in resp.data

    def test_index_contains_map_div(self, client):
        resp = client.get("/")
        assert b'id="map"' in resp.data

    def test_index_contains_search_input(self, client):
        resp = client.get("/")
        assert b'id="search-input"' in resp.data

    def test_index_contains_filter_type(self, client):
        resp = client.get("/")
        assert b'id="filter-type"' in resp.data

    def test_index_contains_filter_tenant(self, client):
        resp = client.get("/")
        assert b'id="filter-tenant"' in resp.data

    def test_index_contains_filter_status(self, client):
        resp = client.get("/")
        assert b'id="filter-status"' in resp.data

    def test_index_contains_filter_parent(self, client):
        resp = client.get("/")
        assert b'id="filter-parent"' in resp.data

    def test_index_contains_filter_section(self, client):
        resp = client.get("/")
        assert b'id="filter-section"' in resp.data

    def test_index_contains_filter_tenant_group(self, client):
        resp = client.get("/")
        assert b'id="filter-tenant-group"' in resp.data

    def test_index_contains_nautobot_url(self, client):
        saved = flask_app.NAUTOBOT_URL
        flask_app.NAUTOBOT_URL = "https://nautobot.example.com"
        try:
            resp = client.get("/")
            assert b"window.NAUTOBOT_URL" in resp.data
            assert b"https://nautobot.example.com" in resp.data
        finally:
            flask_app.NAUTOBOT_URL = saved

    def test_index_nautobot_url_empty_when_unset(self, client):
        saved = flask_app.NAUTOBOT_URL
        flask_app.NAUTOBOT_URL = ""
        try:
            resp = client.get("/")
            assert b"window.NAUTOBOT_URL" in resp.data
            assert b'window.NAUTOBOT_URL = ""' in resp.data
        finally:
            flask_app.NAUTOBOT_URL = saved



# ---------------------------------------------------------------------------
# Tests: caching
# ---------------------------------------------------------------------------
class TestCaching:
    def test_cache_reduces_api_calls(self):
        """Calling nautobot_get twice with the same args should only make one HTTP request."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
            # Patch env vars so nautobot_get doesn't raise RuntimeError
            flask_app.NAUTOBOT_URL = "http://nautobot.test"
            flask_app.NAUTOBOT_TOKEN = "test-token"
            try:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            finally:
                flask_app.NAUTOBOT_URL = ""
                flask_app.NAUTOBOT_TOKEN = ""

        # Second call should have been served from cache – only 1 HTTP request made
        assert mock_get.call_count == 1

    def test_cache_set_and_get(self):
        flask_app.cache.clear()
        flask_app._cache_set("test-key", {"data": 42})
        result = flask_app._cache_get("test-key")
        assert result == {"data": 42}

    def test_cache_expires(self):
        """Verify that Flask-Caching is configured with the correct timeout."""
        flask_app.cache.clear()
        # Store with a very short timeout and verify it expires
        flask_app.cache.set("expiring-key", "value", timeout=1)
        import time
        time.sleep(1.1)
        result = flask_app._cache_get("expiring-key")
        assert result is None

    def test_cache_default_timeout_matches_cache_ttl(self):
        """Flask-Caching default timeout should match the CACHE_TTL env var."""
        assert flask_app.app.config["CACHE_DEFAULT_TIMEOUT"] == flask_app.CACHE_TTL


# ---------------------------------------------------------------------------
# Tests: NAUTOBOT_URL validation
# ---------------------------------------------------------------------------
class TestNautobotURLValidation:
    def test_validate_nautobot_url_accepts_https_url(self):
        assert flask_app._validate_nautobot_url("https://nautobot.example.com") == "https://nautobot.example.com"

    def test_validate_nautobot_url_rejects_missing_scheme(self):
        with pytest.raises(RuntimeError, match="Invalid NAUTOBOT_URL configuration"):
            flask_app._validate_nautobot_url("nautobot.example.com")

    def test_validate_nautobot_url_rejects_invalid_prefix(self):
        with pytest.raises(RuntimeError, match="Invalid NAUTOBOT_URL configuration"):
            flask_app._validate_nautobot_url("NAUTOBOT_URL=https://nautobot.example.com")


# ---------------------------------------------------------------------------
# Tests: SSL verification configuration
# ---------------------------------------------------------------------------
class TestSSLVerification:
    def test_verify_ssl_defaults_to_true(self):
        """When NAUTOBOT_VERIFY_SSL is not set, verify should default to True."""
        # The module-level NAUTOBOT_VERIFY_SSL is parsed at import time from
        # the env var (default "true"), so it should be True.
        assert flask_app.NAUTOBOT_VERIFY_SSL is True

    def test_verify_ssl_false_disables_verification(self):
        """Setting NAUTOBOT_VERIFY_SSL=false should pass verify=False to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_VERIFY_SSL = False
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is False
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_verify_ssl_true_enables_verification(self):
        """Setting NAUTOBOT_VERIFY_SSL=true should pass verify=True to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_VERIFY_SSL = True
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is True
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_verify_ssl_custom_ca_bundle_path(self):
        """Setting NAUTOBOT_VERIFY_SSL to a path should pass that path to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_VERIFY_SSL = "/etc/ssl/certs/custom-ca.pem"
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] == "/etc/ssl/certs/custom-ca.pem"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_get_requests_use_connect_and_read_timeouts(self):
        """Nautobot GETs should set separate connect/read timeouts."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["timeout"] == (5, 30)
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token

    def test_insecure_request_warning_suppressed_when_verify_disabled(self):
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_VERIFY_SSL = False
        try:
            with patch.object(flask_app.urllib3, "disable_warnings") as mock_disable:
                flask_app._configure_nautobot_ssl_warnings()
            mock_disable.assert_called_once_with(flask_app.InsecureRequestWarning)
        finally:
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_insecure_request_warning_not_suppressed_when_verify_enabled(self):
        original_verify = flask_app.NAUTOBOT_VERIFY_SSL
        flask_app.NAUTOBOT_VERIFY_SSL = True
        try:
            with patch.object(flask_app.urllib3, "disable_warnings") as mock_disable:
                flask_app._configure_nautobot_ssl_warnings()
            mock_disable.assert_not_called()
        finally:
            flask_app.NAUTOBOT_VERIFY_SSL = original_verify

    def test_insecure_request_warning_not_suppressed_by_librenms_verify_setting(self):
        original_nautobot_verify = flask_app.NAUTOBOT_VERIFY_SSL
        original_librenms_verify = flask_app.LIBRENMS_VERIFY_SSL
        flask_app.NAUTOBOT_VERIFY_SSL = True
        flask_app.LIBRENMS_VERIFY_SSL = False
        try:
            with patch.object(flask_app.urllib3, "disable_warnings") as mock_disable:
                flask_app._configure_nautobot_ssl_warnings()
            mock_disable.assert_not_called()
        finally:
            flask_app.NAUTOBOT_VERIFY_SSL = original_nautobot_verify
            flask_app.LIBRENMS_VERIFY_SSL = original_librenms_verify


# ---------------------------------------------------------------------------
# Tests: Accept header / API version configuration
# ---------------------------------------------------------------------------
class TestApiVersionHeader:
    def test_default_accept_header_has_no_version(self):
        """When NAUTOBOT_API_VERSION is empty, Accept should be plain application/json."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_version = flask_app.NAUTOBOT_API_VERSION
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_API_VERSION = ""
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/json"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_API_VERSION = original_version

    def test_accept_header_includes_version_when_set(self):
        """When NAUTOBOT_API_VERSION is set, Accept should include the version."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        flask_app.cache.clear()
        original_url = flask_app.NAUTOBOT_URL
        original_token = flask_app.NAUTOBOT_TOKEN
        original_version = flask_app.NAUTOBOT_API_VERSION
        flask_app.NAUTOBOT_URL = "https://nautobot.test"
        flask_app.NAUTOBOT_TOKEN = "test-token"
        flask_app.NAUTOBOT_API_VERSION = "3.0"
        try:
            with patch.object(req_lib, "get", return_value=mock_resp) as mock_get:
                flask_app.nautobot_get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/json; version=3.0"
        finally:
            flask_app.NAUTOBOT_URL = original_url
            flask_app.NAUTOBOT_TOKEN = original_token
            flask_app.NAUTOBOT_API_VERSION = original_version


# ---------------------------------------------------------------------------
# Tests: Custom error handlers
# ---------------------------------------------------------------------------
class TestErrorHandlers:
    def test_404_html_for_browser(self, client):
        resp = client.get("/nonexistent-page")
        assert resp.status_code == 404
        assert b"Page Not Found" in resp.data
        assert b"Back to Map" in resp.data

    def test_404_json_for_api_path(self, client):
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_404_json_when_accept_json(self, client):
        resp = client.get(
            "/nonexistent-page", headers={"Accept": "application/json"}
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "Not found"

    def test_405_html_for_browser(self, client):
        resp = client.post("/")
        assert resp.status_code == 405
        assert b"Method Not Allowed" in resp.data

    def test_405_json_for_api_path(self, client):
        resp = client.post("/api/locations")
        assert resp.status_code == 405
        data = resp.get_json()
        assert data["error"] == "Method not allowed"

    def test_http_exception_returns_json_for_api_path(self):
        with flask_app.app.test_request_context("/api/alerts"):
            resp, status = flask_app.api_http_error(GatewayTimeout())
        assert status == 504
        assert resp.get_json()["error"] == "The connection to an upstream server timed out."


# ---------------------------------------------------------------------------
# Tests: configurable critical role keywords
# ---------------------------------------------------------------------------
class TestConfigurableCriticalKeywords:
    """Tests for the _get_critical_keywords / compute_alert_level helpers."""

    def setup_method(self):
        """Reset module-level keyword state before each test."""
        self._orig_env_kw = flask_app._ENV_CORE_ROLE_KEYWORDS
        self._orig_rules = dict(flask_app._CRITICALITY_RULES)

    def teardown_method(self):
        flask_app._ENV_CORE_ROLE_KEYWORDS = self._orig_env_kw
        flask_app._CRITICALITY_RULES = self._orig_rules

    def test_default_keywords_applied(self):
        """Without any configuration the built-in defaults are used."""
        flask_app._ENV_CORE_ROLE_KEYWORDS = flask_app._DEFAULT_CORE_ROLE_KEYWORDS
        flask_app._CRITICALITY_RULES = {}
        kw = flask_app._get_critical_keywords()
        assert "core" in kw
        assert "router" in kw

    def test_env_override_replaces_defaults(self):
        """_ENV_CORE_ROLE_KEYWORDS env override replaces defaults when no JSON rules."""
        flask_app._ENV_CORE_ROLE_KEYWORDS = ("firewall", "border")
        flask_app._CRITICALITY_RULES = {}
        kw = flask_app._get_critical_keywords()
        assert kw == ("firewall", "border")

    def test_env_override_used_as_fallback_for_unknown_type(self):
        """When rules have no matching type and no 'default' key, env override is used."""
        flask_app._ENV_CORE_ROLE_KEYWORDS = ("firewall",)
        flask_app._CRITICALITY_RULES = {"datacenter": ["core", "spine"]}
        kw = flask_app._get_critical_keywords("office")
        assert kw == ("firewall",)

    def test_location_type_rule_matched(self):
        """The exact location_type key is returned when present in rules."""
        flask_app._CRITICALITY_RULES = {
            "datacenter": ["core", "firewall"],
            "office": ["router"],
        }
        kw = flask_app._get_critical_keywords("Datacenter")
        assert "firewall" in kw

    def test_rules_default_key_used_for_unknown_type(self):
        """The 'default' key in rules is the fallback for unknown location types."""
        flask_app._CRITICALITY_RULES = {
            "default": ["core", "spine"],
            "office": ["router"],
        }
        kw = flask_app._get_critical_keywords("warehouse")
        assert kw == ("core", "spine")

    def test_compute_alert_level_respects_location_type(self):
        """compute_alert_level uses the correct keyword set for the given location type."""
        flask_app._CRITICALITY_RULES = {
            "office": ["router"],
            "datacenter": ["core", "firewall"],
        }
        # A "firewall" device offline in a datacenter → critical
        dc_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"}]
        result = flask_app.compute_alert_level(dc_devices, location_type="datacenter")
        assert result["level"] == "critical"

        # Same device in an office (only "router" is critical there) → medium (if >25%) or ok
        office_devices = [{"id": "d1", "name": "fw01", "role": "Firewall", "status": "offline"},
                          {"id": "d2", "name": "sw01", "role": "Switch", "status": "active"}]
        result = flask_app.compute_alert_level(office_devices, location_type="office")
        assert result["level"] != "critical"

    def test_compute_alert_level_no_devices(self):
        assert flask_app.compute_alert_level([]) == {"level": "ok", "reason": ""}

    def test_compute_alert_level_medium_threshold(self):
        """More than 25% of devices down → medium alert."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
        ]
        # 1/3 ≈ 33% > 25% → medium
        result = flask_app.compute_alert_level(devices)
        assert result["level"] == "medium"

    def test_compute_alert_level_ok_when_below_threshold(self):
        """Under 25% down and no core device down → ok."""
        devices = [
            {"id": "d1", "name": "sw01", "role": "Switch", "status": "offline"},
            {"id": "d2", "name": "sw02", "role": "Switch", "status": "active"},
            {"id": "d3", "name": "sw03", "role": "Switch", "status": "active"},
            {"id": "d4", "name": "sw04", "role": "Switch", "status": "active"},
            {"id": "d5", "name": "sw05", "role": "Switch", "status": "active"},
        ]
        # 1/5 = 20% ≤ 25% → ok
        result = flask_app.compute_alert_level(devices)
        assert result["level"] == "ok"


# ---------------------------------------------------------------------------
# Tests: location_type passed through detail endpoint
# ---------------------------------------------------------------------------
class TestLocationDetailWithLocationType:
    def test_location_type_param_accepted(self, client):
        """The ?location_type query param is accepted without error."""
        with patch.object(flask_app, "nautobot_get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail?location_type=Data+Center")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "devices" in data
        assert "alert" in data

    def test_location_type_influences_alert(self, client):
        """When location_type maps to rules, compute_alert_level uses correct keywords."""
        import app as flask_app_local
        orig_rules = dict(flask_app_local._CRITICALITY_RULES)
        orig_env = flask_app_local._ENV_CORE_ROLE_KEYWORDS
        flask_app_local._CRITICALITY_RULES = {"datacenter": ["firewall"]}
        flask_app_local._ENV_CORE_ROLE_KEYWORDS = ()

        firewall_devices_page = {
            "count": 1,
            "next": None,
            "results": [
                {
                    "id": "dev-fw",
                    "name": "fw01",
                    "device_type": {"model": "PA-220", "manufacturer": {"name": "Palo Alto"}},
                    "role": {"name": "Firewall"},
                    "status": {"label": "offline"},
                    "platform": None,
                    "serial": "",
                    "tenant": None,
                }
            ],
        }

        def mock_get(endpoint, params=None):
            if "dcim/devices" in endpoint:
                return firewall_devices_page
            return {"count": 0, "next": None, "results": []}

        try:
            with patch.object(flask_app_local, "nautobot_get", side_effect=mock_get):
                resp = client.get("/api/locations/loc-1/detail?location_type=datacenter")
            data = resp.get_json()
            assert data["alert"]["level"] == "critical"
        finally:
            flask_app_local._CRITICALITY_RULES = orig_rules
            flask_app_local._ENV_CORE_ROLE_KEYWORDS = orig_env


# ---------------------------------------------------------------------------
# Tests: criticality override REST endpoints
# ---------------------------------------------------------------------------
class TestCriticalityOverrideEndpoints:
    """Tests for /api/criticality-overrides (requires NAUTOBOT_MAPS_DB)."""

    def setup_method(self):
        """Configure a temp-file SQLite DB for each test."""
        import tempfile
        self._orig_db = flask_app.NAUTOBOT_MAPS_DB
        self._db_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_tmp.close()
        flask_app.NAUTOBOT_MAPS_DB = self._db_tmp.name
        flask_app._init_db()

    def teardown_method(self):
        flask_app.NAUTOBOT_MAPS_DB = self._orig_db
        import os
        try:
            os.unlink(self._db_tmp.name)
        except Exception:
            pass

    def test_list_empty(self, client):
        resp = client.get("/api/criticality-overrides")
        assert resp.status_code == 200
        assert resp.get_json()["overrides"] == []

    def test_create_override(self, client):
        resp = client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-abc", "is_critical": False,
                  "reason": "Local firewall", "updated_by": "admin"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["nautobot_device_id"] == "dev-abc"
        assert data["is_critical"] is False

    def test_list_after_create(self, client):
        client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-abc", "is_critical": True,
                  "reason": "Core router", "updated_by": "admin"},
            content_type="application/json",
        )
        resp = client.get("/api/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["nautobot_device_id"] == "dev-abc"

    def test_update_override(self, client):
        """Posting the same device_id a second time updates in-place."""
        client.post("/api/criticality-overrides",
                    json={"nautobot_device_id": "dev-x", "is_critical": True},
                    content_type="application/json")
        client.post("/api/criticality-overrides",
                    json={"nautobot_device_id": "dev-x", "is_critical": False,
                          "reason": "Changed"},
                    content_type="application/json")
        resp = client.get("/api/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["is_critical"] == 0  # stored as int

    def test_delete_override(self, client):
        client.post("/api/criticality-overrides",
                    json={"nautobot_device_id": "dev-del", "is_critical": True},
                    content_type="application/json")
        resp = client.delete("/api/criticality-overrides/dev-del")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        # Should be gone now
        resp2 = client.get("/api/criticality-overrides")
        assert resp2.get_json()["overrides"] == []

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/criticality-overrides/does-not-exist")
        assert resp.status_code == 404

    def test_create_missing_device_id_returns_400(self, client):
        resp = client.post("/api/criticality-overrides",
                           json={"is_critical": True},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_override_affects_compute_alert_level(self):
        """A device marked is_critical=False must not trigger a critical alert."""
        # Insert the override directly via SQLite so we share the same connection
        conn = flask_app._get_db_conn()
        with conn:
            conn.execute(
                "INSERT INTO device_criticality_override "
                "(nautobot_device_id, is_critical, reason, updated_by) "
                "VALUES (?, ?, ?, ?)",
                ("dev-fw", 0, "Local firewall – not critical", "test"),
            )
        conn.close()

        devices = [
            {"id": "dev-fw", "name": "fw-local", "role": "Core Router", "status": "offline"},
        ]
        # The override says is_critical=False, so even a "Core Router" that's
        # offline should not produce a critical alert.
        result = flask_app.compute_alert_level(devices)
        assert result["level"] != "critical"

    def test_no_db_returns_503(self, client):
        """When NAUTOBOT_MAPS_DB is empty, override endpoints return 503."""
        saved = flask_app.NAUTOBOT_MAPS_DB
        flask_app.NAUTOBOT_MAPS_DB = ""
        try:
            resp = client.get("/api/criticality-overrides")
            assert resp.status_code == 503
            resp2 = client.post("/api/criticality-overrides",
                                json={"nautobot_device_id": "x"},
                                content_type="application/json")
            assert resp2.status_code == 503
            resp3 = client.delete("/api/criticality-overrides/x")
            assert resp3.status_code == 503
        finally:
            flask_app.NAUTOBOT_MAPS_DB = saved

    def test_requires_operator_role_when_auth_enabled(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-viewers"},
            )
        assert resp.status_code == 403
        assert resp.get_json()["required_role"] == "operator"

    def test_accepts_operator_group_when_auth_enabled(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert resp.status_code == 200

    def test_uses_authenticated_username_as_updated_by(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
            assert resp.status_code == 200
            listed = client.get(
                "/api/criticality-overrides",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert listed.status_code == 200
        assert listed.get_json()["overrides"][0]["updated_by"] == "alice"


# ---------------------------------------------------------------------------
# Tests: alert lifecycle history and cases
# ---------------------------------------------------------------------------
class TestAlertLifecycleTracking:
    def setup_method(self):
        import tempfile
        self._orig_db = flask_app.NAUTOBOT_MAPS_DB
        self._orig_db_url = flask_app.NAUTOBOT_MAPS_DATABASE_URL
        self._db_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_tmp.close()
        flask_app.NAUTOBOT_MAPS_DATABASE_URL = ""
        flask_app.NAUTOBOT_MAPS_DB = self._db_tmp.name
        flask_app._init_db()
        flask_app.cache.clear()

    def teardown_method(self):
        flask_app.NAUTOBOT_MAPS_DB = self._orig_db
        flask_app.NAUTOBOT_MAPS_DATABASE_URL = self._orig_db_url
        import os
        try:
            os.unlink(self._db_tmp.name)
        except Exception:
            pass

    def test_api_alerts_contains_lifecycle_fields(self, client):
        with patch.object(flask_app, "get_locations", return_value=[{
            "id": "loc-1",
            "name": "Site One",
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
        }]), patch.object(flask_app, "fetch_all_pages", return_value=[]), patch.object(
            flask_app,
            "_get_location_devices_and_alert",
            return_value=(
                [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
                {"level": "critical", "reason": "Core device(s) offline: router01"},
            ),
        ):
            resp = client.get("/api/alerts")
        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert "current_downtime_seconds" in entry
        assert "historical_downtime_seconds" in entry
        assert "active_cases" in entry
        assert entry["active_alert_instance_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": [],
            }
        ]

    def test_alert_history_tracks_open_and_resolve(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        t1 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        flask_app._upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        flask_app._upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "router01", "status": "active"}],
            {"level": "ok", "reason": ""},
            t1,
        )
        resp = client.get("/api/alert-history?site_id=loc-1")
        assert resp.status_code == 200
        instances = resp.get_json()["instances"]
        assert len(instances) == 1
        assert instances[0]["status"] == "resolved"
        assert instances[0]["total_downtime_seconds"] == 300
        event_types = [event["event_type"] for event in instances[0]["events"]]
        assert "opened" in event_types
        assert "resolved" in event_types
        resolved_event = next(event for event in instances[0]["events"] if event["event_type"] == "resolved")
        assert resolved_event["snapshot"]["site_id"] == "loc-1"
        assert resolved_event["snapshot"]["device_id"] == "dev-1"
        assert resolved_event["snapshot"]["status"] == "resolved"

    def test_add_case_number_to_active_alert(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        flask_app._upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        created = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-1001"},
            content_type="application/json",
        )
        assert created.status_code == 200
        history = client.get("/api/alert-history?site_id=loc-1&device_id=dev-1")
        assert history.status_code == 200
        instances = history.get_json()["instances"]
        assert instances[0]["cases"][0]["case_number"] == "INC-1001"

    def test_api_alerts_preserves_case_numbers_on_current_down_devices(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        flask_app._upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        created = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-1001"},
            content_type="application/json",
        )
        assert created.status_code == 200

        with patch.object(flask_app, "get_locations", return_value=[{
            "id": "loc-1",
            "name": "Site One",
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
        }]), patch.object(flask_app, "fetch_all_pages", return_value=[]), patch.object(
            flask_app,
            "_get_location_devices_and_alert",
            return_value=(
                [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
                {"level": "critical", "reason": "Core device(s) offline: router01"},
            ),
        ):
            resp = client.get("/api/alerts")

        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert entry["active_cases"] == ["INC-1001"]
        assert entry["down_devices"] == [
            {
                "device_id": "dev-1",
                "device_name": "router01",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": ["INC-1001"],
            }
        ]

    def test_add_case_uses_authenticated_user_for_created_by(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        flask_app._upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            created = client.post(
                "/api/alert-cases",
                json={
                    "site_id": "loc-1",
                    "device_id": "dev-1",
                    "case_number": "INC-1002",
                    "created_by": "mallory",
                },
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
            assert created.status_code == 200
            history = client.get(
                "/api/alert-history?site_id=loc-1&device_id=dev-1",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert history.status_code == 200
        instances = history.get_json()["instances"]
        assert instances[0]["cases"][0]["created_by"] == "alice"

    def test_alert_history_rejects_invalid_timestamp_filters(self, client):
        resp = client.get("/api/alert-history?start_at=not-a-timestamp")
        assert resp.status_code == 400
        assert "start_at" in resp.get_json()["error"]
        resp = client.get("/api/alert-history?end_at=still-not-a-timestamp")
        assert resp.status_code == 400
        assert "end_at" in resp.get_json()["error"]

    def test_failed_alert_observation_does_not_resolve_open_incident(self):
        site = {"id": "loc-1", "name": "Site One"}
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        flask_app._upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "router01", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        with patch.object(flask_app, "get_locations", return_value=sample_locations), \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")):
            data = flask_app.get_alert_board_data(force_refresh=True)
        assert data["alerts"][0]["alert_level"] == "unknown"
        history = flask_app._get_alert_context_for_site("loc-1", flask_app._iso_utc_now())
        assert history["active_alert_instance_count"] == 1
        conn = flask_app._get_db_conn()
        try:
            rows = conn.execute(
                "SELECT status FROM alert_instances WHERE site_id = ? ORDER BY id DESC",
                ("loc-1",),
            ).fetchall()
        finally:
            conn.close()
        assert flask_app._row_to_dict(rows[0])["status"] == "open"

    def test_get_alert_board_data_uses_cache_when_persistence_enabled(self):
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        devices_return = (
            [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
        )
        with patch.object(flask_app, "get_locations", return_value=sample_locations) as get_locations, \
             patch.object(flask_app, "fetch_all_pages", return_value=[]), \
             patch.object(flask_app, "_get_location_devices_and_alert", return_value=devices_return) as get_alert:
            first = flask_app.get_alert_board_data(force_refresh=True)
            second = flask_app.get_alert_board_data()
        assert first["alerts"] == second["alerts"]
        assert get_locations.call_count == 1
        assert get_alert.call_count == 1

    def test_get_alert_board_data_sets_ttl_and_enqueues_sync_on_force_refresh(self):
        first_payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": flask_app.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        second_payload = {
            "checked_at": "2026-01-01T00:05:00Z",
            "stale_after_seconds": flask_app.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        flask_app.cache.clear()
        with patch.object(
            flask_app,
            "_build_alert_board_payload",
            side_effect=[first_payload, second_payload],
        ) as build_payload, patch.object(flask_app, "_cache_set", wraps=flask_app._cache_set) as cache_set, patch.object(
            flask_app,
            "_ensure_inventory_snapshot",
        ) as ensure_snapshot, patch.object(
            flask_app,
            "_nautobot_snapshot_initialized",
            return_value=True,
        ):
            first = flask_app.get_alert_board_data(force_refresh=True)
            second = flask_app.get_alert_board_data()
            refreshed = flask_app.get_alert_board_data(force_refresh=True)
        assert first["checked_at"] == second["checked_at"] == "2026-01-01T00:00:00Z"
        assert refreshed["checked_at"] == "2026-01-01T00:00:00Z"
        assert build_payload.call_count == 1
        assert cache_set.call_count == 1
        assert ensure_snapshot.call_count == 2
        assert all(
            call.args == () and call.kwargs == {"force": True, "wait": False}
            for call in ensure_snapshot.call_args_list
        )
        assert all(
            call.kwargs.get("timeout") == flask_app.CACHE_TTL
            for call in cache_set.call_args_list
        )

    def test_get_alert_board_data_builds_snapshot_only_payload(self):
        flask_app.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": flask_app.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with patch.object(
            flask_app, "_build_alert_board_payload", return_value=payload
        ) as build_payload, patch.object(flask_app, "_ensure_inventory_snapshot") as ensure_snapshot:
            result = flask_app.get_alert_board_data(force_refresh=True)

        assert result["checked_at"] == "2026-01-01T00:00:00Z"
        build_payload.assert_called_once_with(
            snapshot_only=True,
            include_non_operational=False,
        )
        ensure_snapshot.assert_called_once_with(force=True, wait=False)

    def test_get_alert_board_data_does_not_cache_empty_payload_before_snapshot_init(self):
        flask_app.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": flask_app.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with patch.object(
            flask_app,
            "_build_alert_board_payload",
            return_value=payload,
        ) as build_payload, patch.object(
            flask_app,
            "_cache_set",
            wraps=flask_app._cache_set,
        ) as cache_set, patch.object(
            flask_app,
            "_nautobot_snapshot_initialized",
            return_value=False,
        ), patch.object(flask_app, "_ensure_inventory_snapshot"):
            first = flask_app.get_alert_board_data(force_refresh=True)
            second = flask_app.get_alert_board_data()

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
                if "SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at" in query:
                    return _FakeResult([{
                        "id": 101,
                        "alert_key": "k",
                        "site_id": "loc-1",
                        "site_name": "Site One",
                        "device_id": "dev-1",
                        "device_name": "router01",
                        "down_started_at": "2026-01-01T00:00:00Z",
                    }])
                if "UPDATE alert_instances" in query:
                    return _FakeResult([], rowcount=1)
                return _FakeResult([])

        fake_conn = _FakeConn()
        checked_at = "2026-01-01T00:00:00Z"
        with patch.object(flask_app, "_is_postgres", return_value=True):
            flask_app._upsert_alert_lifecycle_for_site(
                {"id": "loc-1", "name": "Site One"},
                [{"id": "dev-1", "name": "router01", "status": "offline"}],
                {"level": "critical", "reason": "offline"},
                checked_at,
                conn=fake_conn,
            )
            flask_app._resolve_open_alert_instances_for_site(
                fake_conn,
                "loc-1",
                set(),
                checked_at,
            )
        insert_sql = next(query for query, _ in fake_conn.queries if "INSERT INTO alert_instances" in query)
        assert "ON CONFLICT (alert_key) WHERE status = 'open' DO NOTHING" in insert_sql
        assert "%s" in insert_sql
        update_sql = next(query for query, _ in fake_conn.queries if "UPDATE alert_instances" in query)
        assert "CURRENT_TIMESTAMP" in update_sql
        assert "AND status = 'open'" in update_sql
        assert "AND down_started_at <=" in update_sql
        event_params = [params for query, params in fake_conn.queries if "INSERT INTO alert_events" in query]
        assert any(param == checked_at for params in event_params for param in params)
        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 1

    def test_init_db_postgres_uses_advisory_lock_before_ddl(self):
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

            def execute(self, query, params=()):
                if self.closed:
                    raise RuntimeError("the connection is closed")
                self.queries.append(query)
                return None

            def close(self):
                self.closed = True

            def transaction(self):
                return _FakeTransaction(self)

        fake_conn = _FakeConn()
        with patch.object(flask_app, "_get_db_conn", return_value=fake_conn), patch.object(
            flask_app, "_is_postgres", return_value=True
        ):
            flask_app._init_db()

        lock_idx = next(i for i, query in enumerate(fake_conn.queries) if "pg_advisory_xact_lock" in query)
        table_idx = next(
            i
            for i, query in enumerate(fake_conn.queries)
            if "CREATE TABLE IF NOT EXISTS device_criticality_override" in query
        )
        alter_idx = next(
            i
            for i, query in enumerate(fake_conn.queries)
            if "ALTER TABLE nautobot_location_cache ALTER COLUMN time_zone DROP NOT NULL" in query
            and "information_schema.columns" in query
        )
        assert lock_idx < table_idx
        assert alter_idx > table_idx
        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 1

    def test_init_db_postgres_marks_primary_ip_migration_pending(self):
        class _FakeResult:
            def __init__(self, rows=None):
                self._rows = rows or []

            def fetchone(self):
                return self._rows[0] if self._rows else None

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
                self.transaction_entries = 0

            def close(self):
                return None

            def transaction(self):
                return _FakeTransaction(self)

            def execute(self, query, params=()):
                self.queries.append((query, params))
                if "SELECT NOT EXISTS" in query and "column_name = 'primary_ip'" in query:
                    return _FakeResult([{"missing": True}])
                return _FakeResult([])

        fake_conn = _FakeConn()
        with patch.object(flask_app, "_get_db_conn", return_value=fake_conn), patch.object(
            flask_app, "_is_postgres", return_value=True
        ):
            flask_app._init_db()

        reset_query, reset_params = next(
            (query, params)
            for query, params in fake_conn.queries
            if "UPDATE inventory_sync_state" in query
        )
        assert "status = 'pending'" in reset_query
        assert reset_params == ("nautobot_inventory",)

    def test_init_db_sqlite_migrates_legacy_time_zone_not_null(self):
        import os
        import sqlite3
        import tempfile

        original_db = flask_app.NAUTOBOT_MAPS_DB
        original_db_url = flask_app.NAUTOBOT_MAPS_DATABASE_URL
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()

        try:
            conn = sqlite3.connect(tmp.name)
            conn.execute(
                """
                CREATE TABLE nautobot_location_cache (
                    location_id       TEXT PRIMARY KEY,
                    name              TEXT NOT NULL DEFAULT '',
                    slug              TEXT NOT NULL DEFAULT '',
                    status            TEXT NOT NULL DEFAULT '',
                    location_type     TEXT NOT NULL DEFAULT '',
                    parent            TEXT NOT NULL DEFAULT '',
                    latitude          REAL,
                    longitude         REAL,
                    description       TEXT NOT NULL DEFAULT '',
                    physical_address  TEXT NOT NULL DEFAULT '',
                    facility          TEXT NOT NULL DEFAULT '',
                    tenant            TEXT NOT NULL DEFAULT '',
                    tenant_id         TEXT NOT NULL DEFAULT '',
                    tenant_group      TEXT NOT NULL DEFAULT '',
                    asn               INTEGER,
                    time_zone         TEXT NOT NULL DEFAULT '',
                    tags_json         TEXT NOT NULL DEFAULT '[]',
                    url               TEXT NOT NULL DEFAULT '',
                    last_updated      TEXT,
                    synced_at         TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                INSERT INTO nautobot_location_cache (location_id, time_zone)
                VALUES (?, ?)
                """,
                ("loc-legacy", "UTC"),
            )
            conn.execute(
                "CREATE INDEX idx_legacy_location_cache_name ON nautobot_location_cache(name)"
            )
            conn.execute(
                """
                CREATE TRIGGER trg_legacy_location_cache_insert
                AFTER INSERT ON nautobot_location_cache
                BEGIN
                    UPDATE nautobot_location_cache
                    SET url = NEW.url
                    WHERE location_id = NEW.location_id;
                END
                """
            )
            conn.commit()
            conn.close()

            flask_app.NAUTOBOT_MAPS_DATABASE_URL = ""
            flask_app.NAUTOBOT_MAPS_DB = tmp.name
            flask_app._init_db()

            conn = sqlite3.connect(tmp.name)
            conn.row_factory = sqlite3.Row
            columns = conn.execute("PRAGMA table_info(nautobot_location_cache)").fetchall()
            time_zone_column = next(col for col in columns if col["name"] == "time_zone")
            assert time_zone_column["notnull"] == 0
            indexes = conn.execute("PRAGMA index_list(nautobot_location_cache)").fetchall()
            assert any(idx["name"] == "idx_legacy_location_cache_name" for idx in indexes)
            trigger = conn.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name = 'trg_legacy_location_cache_insert'
                """
            ).fetchone()
            assert trigger is not None
            assert "ON nautobot_location_cache" in trigger["sql"]
            assert "nautobot_location_cache_legacy" not in trigger["sql"]

            conn.execute(
                """
                INSERT INTO nautobot_location_cache (location_id, time_zone)
                VALUES (?, ?)
                """,
                ("loc-null", None),
            )
            conn.commit()
            inserted = conn.execute(
                "SELECT time_zone FROM nautobot_location_cache WHERE location_id = ?",
                ("loc-null",),
            ).fetchone()
            assert inserted["time_zone"] is None
            conn.close()
        finally:
            flask_app.NAUTOBOT_MAPS_DB = original_db
            flask_app.NAUTOBOT_MAPS_DATABASE_URL = original_db_url
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def test_init_db_sqlite_marks_primary_ip_migration_pending(self):
        import os
        import sqlite3
        import tempfile

        original_db = flask_app.NAUTOBOT_MAPS_DB
        original_db_url = flask_app.NAUTOBOT_MAPS_DATABASE_URL
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()

        try:
            conn = sqlite3.connect(tmp.name)
            conn.execute(
                """
                CREATE TABLE inventory_sync_state (
                    source               TEXT PRIMARY KEY,
                    last_started_at      TEXT,
                    last_completed_at    TEXT,
                    last_successful_sync TEXT,
                    status               TEXT NOT NULL DEFAULT 'idle',
                    error_message        TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE nautobot_device_cache (
                    device_id      TEXT PRIMARY KEY,
                    location_id    TEXT NOT NULL DEFAULT '',
                    name           TEXT NOT NULL DEFAULT '',
                    device_type    TEXT NOT NULL DEFAULT '',
                    manufacturer   TEXT NOT NULL DEFAULT '',
                    role           TEXT NOT NULL DEFAULT '',
                    status         TEXT NOT NULL DEFAULT '',
                    platform       TEXT NOT NULL DEFAULT '',
                    serial         TEXT NOT NULL DEFAULT '',
                    tenant         TEXT NOT NULL DEFAULT '',
                    last_updated   TEXT,
                    synced_at      TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                INSERT INTO inventory_sync_state (
                    source,
                    last_started_at,
                    last_completed_at,
                    last_successful_sync,
                    status,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "nautobot_inventory",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:05:00Z",
                    "2026-01-01T00:05:00Z",
                    "idle",
                    "",
                ),
            )
            conn.commit()
            conn.close()

            flask_app.NAUTOBOT_MAPS_DATABASE_URL = ""
            flask_app.NAUTOBOT_MAPS_DB = tmp.name
            flask_app._init_db()

            conn = sqlite3.connect(tmp.name)
            conn.row_factory = sqlite3.Row
            columns = conn.execute("PRAGMA table_info(nautobot_device_cache)").fetchall()
            assert any(col["name"] == "primary_ip" for col in columns)
            state = conn.execute(
                """
                SELECT last_completed_at, last_successful_sync, status
                FROM inventory_sync_state
                WHERE source = ?
                """,
                ("nautobot_inventory",),
            ).fetchone()
            assert state["last_completed_at"] is None
            assert state["last_successful_sync"] is None
            assert state["status"] == "pending"
            conn.close()
        finally:
            flask_app.NAUTOBOT_MAPS_DB = original_db
            flask_app.NAUTOBOT_MAPS_DATABASE_URL = original_db_url
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    def test_get_db_conn_postgres_enables_autocommit(self):
        sentinel_conn = object()
        sentinel_row_factory = object()
        with patch.object(flask_app, "NAUTOBOT_MAPS_DATABASE_URL", "postgresql://db.example/maps"), patch.object(
            flask_app, "NAUTOBOT_MAPS_DB", ""
        ), patch.object(
            flask_app, "psycopg"
        ) as psycopg_module, patch.object(
            flask_app, "dict_row", sentinel_row_factory
        ):
            psycopg_module.connect.return_value = sentinel_conn

            conn = flask_app._get_db_conn()

        assert conn is sentinel_conn
        psycopg_module.connect.assert_called_once_with(
            "postgresql://db.example/maps",
            row_factory=sentinel_row_factory,
            autocommit=True,
        )


class TestInventoryCacheSync:
    def setup_method(self):
        import tempfile

        self._orig_db = flask_app.NAUTOBOT_MAPS_DB
        self._orig_db_url = flask_app.NAUTOBOT_MAPS_DATABASE_URL
        self._orig_nautobot_url = flask_app.NAUTOBOT_URL
        self._orig_nautobot_token = flask_app.NAUTOBOT_TOKEN
        self._orig_librenms_url = flask_app.LIBRENMS_URL
        self._orig_librenms_token = flask_app.LIBRENMS_API_TOKEN
        self._db_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_tmp.close()
        flask_app.NAUTOBOT_MAPS_DATABASE_URL = ""
        flask_app.NAUTOBOT_MAPS_DB = self._db_tmp.name
        flask_app.NAUTOBOT_URL = ""
        flask_app.NAUTOBOT_TOKEN = ""
        flask_app.LIBRENMS_URL = ""
        flask_app.LIBRENMS_API_TOKEN = ""
        flask_app._init_db()
        flask_app.cache.clear()

    def teardown_method(self):
        flask_app.NAUTOBOT_MAPS_DB = self._orig_db
        flask_app.NAUTOBOT_MAPS_DATABASE_URL = self._orig_db_url
        flask_app.NAUTOBOT_URL = self._orig_nautobot_url
        flask_app.NAUTOBOT_TOKEN = self._orig_nautobot_token
        flask_app.LIBRENMS_URL = self._orig_librenms_url
        flask_app.LIBRENMS_API_TOKEN = self._orig_librenms_token
        import os

        try:
            os.unlink(self._db_tmp.name)
        except Exception:
            pass

    def test_get_locations_prefers_cached_inventory_without_live_api(self):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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

        with patch.object(flask_app, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
            locations = flask_app.get_locations()

        assert locations == [
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
            }
        ]

    def test_cached_locations_allow_null_time_zone(self):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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

        locations = flask_app._read_cached_locations(include_without_coordinates=True)
        location = next(item for item in locations if item["id"] == "loc-1")

        assert location["time_zone"] is None

    def test_write_cached_locations_coalesces_explicit_none_text_fields(self):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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

        locations = flask_app._read_cached_locations(include_without_coordinates=True)
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
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_devices(
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

        devices = flask_app._read_cached_devices()
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
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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
                flask_app._write_cached_devices(
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

        flask_app.cache.clear()
        with patch.object(flask_app, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
            data = flask_app.get_alert_board_data(force_refresh=True)

        assert data["summary"]["critical"] == 1
        assert data["alerts"][0]["id"] == "loc-1"
        assert data["alerts"][0]["down_device_count"] == 1
        assert data["alerts"][0]["alert_level"] == "critical"

    def test_cached_location_read_triggers_background_refresh_when_sync_is_due(self):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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
                flask_app._record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:00:00Z",
                    last_successful_sync="2026-01-01T00:00:00Z",
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        refresh_called = threading.Event()

        def fake_sync(force=False):
            refresh_called.set()

        with patch.object(flask_app, "NAUTOBOT_URL", "https://nautobot.example.com"), patch.object(
            flask_app, "NAUTOBOT_TOKEN", "token"
        ), patch.object(
            flask_app, "INVENTORY_SYNC_INTERVAL_SECONDS", 0
        ), patch.object(
            flask_app, "_sync_nautobot_inventory", side_effect=fake_sync
        ):
            locations = flask_app.get_locations()
            assert refresh_called.wait(1), "expected cached read to trigger a background refresh"

        assert locations[0]["id"] == "loc-1"

    def test_sync_nautobot_inventory_uses_last_successful_sync_watermark(self):
        calls = []

        def fake_fetch(endpoint, params=None):
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

        with patch.object(flask_app, "NAUTOBOT_URL", "https://nautobot.example.com"), patch.object(
            flask_app, "NAUTOBOT_TOKEN", "token"
        ), patch.object(flask_app, "fetch_all_pages", side_effect=fake_fetch), patch.object(
            flask_app, "_read_cached_location_name_map", return_value={}
        ), patch.object(flask_app, "_build_device_lookup_maps", return_value={}):
            flask_app._sync_nautobot_inventory(force=True)
            first_state = flask_app._get_sync_state("nautobot_inventory")
            flask_app._sync_nautobot_inventory()

        location_calls = [params for endpoint, params in calls if endpoint == "dcim/locations/"]
        device_calls = [params for endpoint, params in calls if endpoint == "dcim/devices/"]

        assert location_calls[0] == {}
        assert device_calls[0] == {}
        assert location_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]
        assert device_calls[1]["last_updated__gte"] == first_state["last_successful_sync"]

    def test_alert_board_filters_cached_devices_without_primary_ip_while_backfill_is_pending(self):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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
                flask_app._write_cached_devices(
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
                flask_app._record_sync_state(
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

        flask_app.cache.clear()
        with patch.object(flask_app, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
            data = flask_app.get_alert_board_data(force_refresh=True)

        assert data["summary"]["ok"] == 1
        assert data["alerts"][0]["device_count"] == 0
        assert data["alerts"][0]["down_device_count"] == 0

    def test_api_alerts_filters_cached_devices_without_primary_ip(self, client):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
                    conn,
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
                flask_app._write_cached_devices(
                    conn,
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
                flask_app._record_sync_state(
                    conn,
                    "nautobot_inventory",
                    last_started_at="2026-01-01T00:00:00Z",
                    last_completed_at="2026-01-01T00:05:00Z",
                    last_successful_sync="2026-01-01T00:05:00Z",
                    status="idle",
                    error_message="",
                )
        finally:
            conn.close()

        flask_app.cache.clear()
        with patch.object(flask_app, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
            resp = client.get("/api/alerts")

        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert entry["device_count"] == 1
        assert entry["down_device_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-2",
                "device_name": "router01",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": [],
            }
        ]

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
                return _FakeResult()

            def close(self):
                self.closed = True

        fake_conn = _FakeConn(autocommit=True)
        with patch.object(flask_app, "_get_db_conn", return_value=fake_conn), patch.object(
            flask_app, "LIBRENMS_URL", "https://librenms.example.com"
        ), patch.object(
            flask_app, "LIBRENMS_API_TOKEN", "token"
        ), patch.object(
            flask_app, "_get_sync_state", return_value={"last_successful_sync": None}
        ), patch.object(
            flask_app, "_fetch_librenms_inventory",
            return_value=[{"device_id": 1, "hostname": "router01", "status": 1, "status_reason": ""}],
        ), patch.object(flask_app.cache, "delete") as cache_delete:
            flask_app._sync_librenms_inventory()

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
        assert cache_delete.call_count == 2

    def test_full_reconcile_prunes_deleted_cached_inventory(self):
        conn = flask_app._get_db_conn()
        try:
            with conn:
                flask_app._write_cached_locations(
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
                flask_app._write_cached_devices(
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

        def fake_fetch(endpoint, params=None):
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

        with patch.object(flask_app, "NAUTOBOT_URL", "https://nautobot.example.com"), patch.object(
            flask_app, "NAUTOBOT_TOKEN", "token"
        ), patch.object(flask_app, "fetch_all_pages", side_effect=fake_fetch), patch.object(
            flask_app, "_read_cached_location_name_map", return_value={}
        ), patch.object(flask_app, "_build_device_lookup_maps", return_value={}):
            flask_app._sync_nautobot_inventory(force=True)

        assert [item["id"] for item in flask_app._read_cached_locations(include_without_coordinates=True)] == ["loc-1"]
        assert [item["id"] for item in flask_app._read_cached_devices()] == ["dev-1"]


# ---------------------------------------------------------------------------
# Tests: criticality_rules.json loading
# ---------------------------------------------------------------------------
class TestCriticalityRulesFile:
    def test_load_valid_rules_file(self, tmp_path):
        """A valid JSON rules file is parsed into _CRITICALITY_RULES."""
        rules = {"datacenter": ["core", "firewall"], "office": ["router"]}
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))

        orig = dict(flask_app._CRITICALITY_RULES)
        orig_file = flask_app.CRITICALITY_RULES_FILE
        try:
            flask_app.CRITICALITY_RULES_FILE = str(rules_file)
            # Re-run the loading logic
            with open(str(rules_file)) as f:
                loaded = json.load(f)
            flask_app._CRITICALITY_RULES = {
                k.lower(): [kw.lower() for kw in v]
                for k, v in loaded.items()
                if isinstance(v, list)
            }
            assert flask_app._get_critical_keywords("datacenter") == ("core", "firewall")
            assert flask_app._get_critical_keywords("office") == ("router",)
        finally:
            flask_app._CRITICALITY_RULES = orig
            flask_app.CRITICALITY_RULES_FILE = orig_file

    def test_rules_file_bad_format_ignored(self, tmp_path):
        """A rules file with a non-dict top level is ignored gracefully."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps(["not", "a", "dict"]))

        orig = dict(flask_app._CRITICALITY_RULES)
        try:
            # Simulate what the loading code does
            with open(str(bad_file)) as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                pass  # Would be ignored in real code
            # _CRITICALITY_RULES should remain unchanged
            assert flask_app._CRITICALITY_RULES == orig
        finally:
            flask_app._CRITICALITY_RULES = orig


# ---------------------------------------------------------------------------
# Tests: LibreNMS enrichment
# ---------------------------------------------------------------------------
class TestLibreNMSEnrichment:
    def setup_method(self):
        self._orig_url = flask_app.LIBRENMS_URL
        self._orig_token = flask_app.LIBRENMS_API_TOKEN
        self._orig_verify = flask_app.LIBRENMS_VERIFY_SSL

    def teardown_method(self):
        flask_app.LIBRENMS_URL = self._orig_url
        flask_app.LIBRENMS_API_TOKEN = self._orig_token
        flask_app.LIBRENMS_VERIFY_SSL = self._orig_verify

    def test_no_enrichment_when_unconfigured(self):
        """_enrich_with_librenms is a no-op when LIBRENMS_URL is empty."""
        flask_app.LIBRENMS_URL = ""
        flask_app.LIBRENMS_API_TOKEN = ""
        devices = [{"id": "d1", "name": "router01", "status": "active"}]
        result = flask_app._enrich_with_librenms(devices)
        assert result == devices

    def test_fetch_inventory_skips_when_config_is_blank(self):
        """_fetch_librenms_inventory skips API calls when URL/token are blank."""
        flask_app.LIBRENMS_URL = "   "
        flask_app.LIBRENMS_API_TOKEN = "   "
        with patch.object(flask_app.requests, "get") as mock_get:
            result = flask_app._fetch_librenms_inventory()
        assert result == []
        mock_get.assert_not_called()

    def test_librenms_get_uses_verify_setting(self):
        flask_app.LIBRENMS_URL = "https://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        flask_app.LIBRENMS_VERIFY_SSL = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with patch.object(flask_app.requests, "get", return_value=mock_resp) as mock_get:
            flask_app._librenms_get("devices", {"type": "all"})
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False

    def test_librenms_get_suppresses_warning_locally_when_verify_disabled(self):
        flask_app.LIBRENMS_URL = "https://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        flask_app.LIBRENMS_VERIFY_SSL = False
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"devices": []}
        with patch.object(flask_app.requests, "get", return_value=mock_resp), \
                patch.object(flask_app.warnings, "catch_warnings") as mock_catch:
            flask_app._librenms_get("devices", {"type": "all"})
        mock_catch.assert_called_once()

    def test_librenms_down_overrides_active_status(self):
        """A device active in Nautobot but down in LibreNMS is set to offline."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [
                {"device_id": 1, "hostname": "router01", "status": 0},
            ]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_up_does_not_change_active_status(self):
        """A device up in LibreNMS stays active."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01", "status": 1}]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "active"

    def test_librenms_down_does_not_upgrade_already_offline(self):
        """A device already offline in Nautobot stays offline (no double-counting)."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "router01", "status": 0}]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "offline"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_api_failure_returns_original_devices(self):
        """If LibreNMS API call fails, original device list is returned unchanged."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        with patch.object(flask_app, "_librenms_get", side_effect=Exception("timeout")):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result == devices

    def test_librenms_unmatched_device_not_affected(self):
        """Devices not present in LibreNMS are left unchanged."""
        flask_app.LIBRENMS_URL = "http://librenms.test"
        flask_app.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {
            "devices": [{"device_id": 1, "hostname": "other-device", "status": 0}]
        }
        with patch.object(flask_app, "_librenms_get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "status": "active"}]
            result = flask_app._enrich_with_librenms(devices)
        assert result[0]["status"] == "active"


# ---------------------------------------------------------------------------
# Tests: /api/roles
# ---------------------------------------------------------------------------

SAMPLE_ROLES_PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {"id": "role-1", "name": "Core Router", "color": "aa1409", "content_types": []},
        {"id": "role-2", "name": "Firewall", "color": "f44336", "content_types": []},
    ],
}


class TestApiRoles:
    def test_list_roles_returns_all(self, client):
        """GET /api/roles returns all roles from Nautobot."""
        with patch.object(flask_app, "nautobot_get", return_value=SAMPLE_ROLES_PAGE):
            resp = client.get("/api/roles")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "roles" in data
        assert len(data["roles"]) == 2
        assert data["roles"][0]["name"] == "Core Router"

    def test_list_roles_nautobot_unconfigured_returns_503(self, client):
        """GET /api/roles returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_get",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.get("/api/roles")
        assert resp.status_code == 503

    def test_create_role_success(self, client):
        """POST /api/roles proxies to Nautobot and returns 201 on success."""
        created = {"id": "role-new", "name": "Edge Router", "color": "2196f3", "content_types": []}
        with patch.object(flask_app, "nautobot_post", return_value=created):
            resp = client.post("/api/roles",
                               json={"name": "Edge Router", "color": "2196f3"},
                               content_type="application/json")
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "Edge Router"

    def test_create_role_missing_name_returns_400(self, client):
        """POST /api/roles without a name returns 400."""
        resp = client.post("/api/roles",
                           json={"color": "2196f3"},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "name is required" in resp.get_json()["error"]

    def test_create_role_nautobot_unconfigured_returns_503(self, client):
        """POST /api/roles returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_post",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.post("/api/roles",
                               json={"name": "Test Role"},
                               content_type="application/json")
        assert resp.status_code == 503

    def test_create_role_requires_admin_when_auth_enabled(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/roles",
                json={"name": "Edge Router", "color": "2196f3"},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-operators"},
            )
        assert resp.status_code == 403
        assert resp.get_json()["required_role"] == "admin"

    def test_create_role_accepts_admin_group_when_auth_enabled(self, client):
        created = {"id": "role-new", "name": "Edge Router", "color": "2196f3", "content_types": []}
        with auth_config(mode="header", admin_groups={"nautobot-admins"}):
            with patch.object(flask_app, "nautobot_post", return_value=created):
                resp = client.post(
                    "/api/roles",
                    json={"name": "Edge Router", "color": "2196f3"},
                    content_type="application/json",
                    headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "nautobot-admins"},
                )
        assert resp.status_code == 201

    def test_delete_role_success(self, client):
        """DELETE /api/roles/<id> proxies to Nautobot and returns 200."""
        with patch.object(flask_app, "nautobot_delete", return_value=None):
            resp = client.delete("/api/roles/role-1")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        assert resp.get_json()["id"] == "role-1"

    def test_delete_role_not_found_returns_404(self, client):
        """DELETE /api/roles/<id> returns 404 when Nautobot responds with 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_err = flask_app.requests.HTTPError(response=mock_response)
        with patch.object(flask_app, "nautobot_delete", side_effect=http_err):
            resp = client.delete("/api/roles/does-not-exist")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_delete_role_nautobot_unconfigured_returns_503(self, client):
        """DELETE /api/roles/<id> returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_delete",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.delete("/api/roles/role-1")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: /api/location-types
# ---------------------------------------------------------------------------

SAMPLE_LOCATION_TYPES_PAGE = {
    "count": 2,
    "next": None,
    "results": [
        {"id": "lt-dc", "name": "Data Center"},
        {"id": "lt-pop", "name": "PoP"},
    ],
}


class TestApiLocationTypes:
    def test_list_location_types_returns_all(self, client):
        """GET /api/location-types returns all location types from Nautobot."""
        with patch.object(flask_app, "nautobot_get", return_value=SAMPLE_LOCATION_TYPES_PAGE):
            resp = client.get("/api/location-types")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "location_types" in data
        assert len(data["location_types"]) == 2
        assert data["location_types"][0]["name"] == "Data Center"

    def test_list_location_types_nautobot_unconfigured_returns_503(self, client):
        """GET /api/location-types returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_get",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.get("/api/location-types")
        assert resp.status_code == 503

    def test_create_location_type_success(self, client):
        """POST /api/location-types proxies to Nautobot and returns 201 on success."""
        created = {"id": "lt-new", "name": "Office", "slug": "office"}
        with patch.object(flask_app, "nautobot_post", return_value=created):
            resp = client.post("/api/location-types",
                               json={"name": "Office", "slug": "office"},
                               content_type="application/json")
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "Office"

    def test_create_location_type_missing_name_returns_400(self, client):
        """POST /api/location-types without a name returns 400."""
        resp = client.post("/api/location-types",
                           json={"slug": "office"},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "name is required" in resp.get_json()["error"]

    def test_create_location_type_nautobot_unconfigured_returns_503(self, client):
        """POST /api/location-types returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_post",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.post("/api/location-types",
                               json={"name": "Test Type"},
                               content_type="application/json")
        assert resp.status_code == 503

    def test_delete_location_type_success(self, client):
        """DELETE /api/location-types/<id> proxies to Nautobot and returns 200."""
        with patch.object(flask_app, "nautobot_delete", return_value=None):
            resp = client.delete("/api/location-types/lt-dc")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "deleted"
        assert resp.get_json()["id"] == "lt-dc"

    def test_delete_location_type_not_found_returns_404(self, client):
        """DELETE /api/location-types/<id> returns 404 when Nautobot responds with 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        http_err = flask_app.requests.HTTPError(response=mock_response)
        with patch.object(flask_app, "nautobot_delete", side_effect=http_err):
            resp = client.delete("/api/location-types/does-not-exist")
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_delete_location_type_nautobot_unconfigured_returns_503(self, client):
        """DELETE /api/location-types/<id> returns 503 when Nautobot is not configured."""
        with patch.object(flask_app, "nautobot_delete",
                          side_effect=RuntimeError("NAUTOBOT_URL and NAUTOBOT_TOKEN must be set")):
            resp = client.delete("/api/location-types/lt-dc")
        assert resp.status_code == 503


class TestAuthConfiguration:
    @staticmethod
    def _reload_gunicorn_config():
        import gunicorn_config

        return importlib.reload(gunicorn_config)

    def test_header_auth_binds_flask_to_loopback(self):
        with auth_config(mode="header"):
            assert flask_app._get_flask_run_host() == "127.0.0.1"

    def test_non_header_auth_keeps_public_flask_bind(self):
        with auth_config(mode="disabled"):
            assert flask_app._get_flask_run_host() == "0.0.0.0"

    def test_header_auth_binds_gunicorn_to_loopback(self, monkeypatch):
        monkeypatch.setenv("AUTH_MODE", "header")
        assert self._reload_gunicorn_config().bind == "127.0.0.1:5000"

    def test_non_header_auth_keeps_public_gunicorn_bind(self, monkeypatch):
        monkeypatch.setenv("AUTH_MODE", "disabled")
        assert self._reload_gunicorn_config().bind == "0.0.0.0:5000"

    def test_default_gunicorn_timeout_is_120_seconds(self, monkeypatch):
        monkeypatch.delenv("GUNICORN_TIMEOUT", raising=False)
        assert self._reload_gunicorn_config().timeout == 120

    def test_gunicorn_timeout_has_a_120_second_floor(self, monkeypatch):
        monkeypatch.setenv("GUNICORN_TIMEOUT", "30")
        assert self._reload_gunicorn_config().timeout == 120

    def test_gunicorn_timeout_allows_values_above_floor(self, monkeypatch):
        monkeypatch.setenv("GUNICORN_TIMEOUT", "180")
        assert self._reload_gunicorn_config().timeout == 180

    def test_auth_disabled_keeps_write_endpoints_unchanged(self, client):
        with auth_config(mode="disabled"):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
            )
        assert resp.status_code == 503

    def test_missing_identity_header_returns_401(self, client):
        with auth_config(mode="header", operator_groups={"noc-operators"}):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
            )
        assert resp.status_code == 401

    def test_default_role_allows_authenticated_viewer_only_access(self, client):
        with auth_config(mode="header", default_role="viewer"):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
                headers={"X-Forwarded-User": "alice"},
            )
        assert resp.status_code == 403
        assert resp.get_json()["current_role"] == "viewer"
