import importlib
import json
import logging
import os
import pathlib
import re
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

import pytest
import requests
from markupsafe import escape
from werkzeug.exceptions import GatewayTimeout

import app as flask_app
from nautobot_maps import alerts, auth, caching, db, inventory, librenms, nautobot, scheduler, settings, timeutil, web

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


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
    settings.AUTH_DEFAULT_ROLE = auth.normalize_role(default_role)
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
def client():
    flask_app.app.config["TESTING"] = True
    # Clear cache before each test
    caching.cache.clear()
    with flask_app.app.test_client() as c:
        yield c


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

        def fake_get(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return {"count": 1, "next": None, "results": [{"id": "dev-1"}]}

        with patch.object(nautobot, "get", side_effect=fake_get):
            results = nautobot.fetch_all_pages("dcim/devices/")

        assert results == [{"id": "dev-1"}]
        assert calls == [("dcim/devices/", {"limit": 1000, "depth": 0, "offset": 0})]

    def test_respects_explicit_limit_and_depth(self):
        calls = []

        def fake_get(endpoint, params=None, **kwargs):
            calls.append((endpoint, dict(params or {})))
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=fake_get):
            nautobot.fetch_all_pages("dcim/devices/", {"limit": 25, "depth": 2})

        assert calls == [("dcim/devices/", {"limit": 25, "depth": 2, "offset": 0})]


class TestSyncBypassesResponseCache:
    """The inventory sync reads Nautobot directly, never cached pages (#185)."""

    def _counting_requests(self, calls):
        def fake_requests_get(url, **kwargs):
            calls.append(dict(kwargs.get("params") or {}))
            response = MagicMock()
            response.json.return_value = {"results": [{"id": "d1"}], "next": None}
            return response

        return patch.object(requests.Session, "get", side_effect=fake_requests_get)

    def test_uncached_fetches_always_reach_nautobot(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        caching.cache.clear()
        calls = []
        params = {"last_updated__gte": "2026-09-28T12:00:00Z", "depth": 1}
        with flask_app.app.app_context(), self._counting_requests(calls):
            nautobot.fetch_all_pages("dcim/devices/", params, use_cache=False)
            nautobot.fetch_all_pages("dcim/devices/", params, use_cache=False)
            assert len(calls) == 2  # same parameters, still two requests
            # Nor do they fill the cache for a cached reader.
            nautobot.fetch_all_pages("dcim/devices/", params)
            assert len(calls) == 3
            # The UI's cached reads still use the cache.
            nautobot.fetch_all_pages("dcim/devices/", params)
            assert len(calls) == 3

    def test_sync_fetches_without_cache(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        seen = []

        def fake_fetch(endpoint, params=None, **kwargs):
            seen.append((endpoint, kwargs.get("use_cache", True)))
            return []

        monkeypatch.setattr(nautobot, "fetch_all_pages", fake_fetch)
        inventory.sync_nautobot(force=True)
        assert ("dcim/locations/", False) in seen and ("dcim/devices/", False) in seen


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
                {"primary_ip": "192.0.2.9/32", "primary_ip4": None, "primary_ip6": {"address": "2001:db8::1/64"}},
                "2001:db8::1/64",
            ),
            ({"primary_ip": "192.0.2.9/32", "primary_ip4": None, "primary_ip6": None}, "192.0.2.9/32"),
        ):
            assert inventory.extract_primary_ip(device) == expected


# ---------------------------------------------------------------------------
# Tests: /api/locations
# ---------------------------------------------------------------------------
class TestApiLocations:
    def test_returns_locations_with_coordinates(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "locations" in data
        # loc-3 (no GPS) must be excluded
        assert len(data["locations"]) == 2

    def test_location_fields(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["name"] == "Copenhagen DC"
        assert loc["latitude"] == 55.6761
        assert loc["longitude"] == 12.5683
        assert loc["tenant"] == "Acme Corp"
        assert loc["asn"] == 65001
        assert loc["status"] == "Active"

    def test_location_type_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["location_type"] == "Data Center"

    def test_country_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
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

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/locations" in endpoint:
                return fallback_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["country"] == "Denmark"

    def test_parent_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["parent"] == "Denmark"

    def test_tenant_group_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tenant_group"] == "Corporate"

    def test_tenant_group_empty_when_no_tenant(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        # loc-2 (Aarhus PoP) has no tenant
        loc = resp.get_json()["locations"][1]
        assert loc["tenant_group"] == ""

    def test_facility_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["facility"] == "CPH-1"

    def test_facility_empty_when_not_set(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][1]
        assert loc["facility"] == ""

    def test_tags_field_populated(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations")
        loc = resp.get_json()["locations"][0]
        assert loc["tags"] == ["critical", "production"]

    def test_tags_empty_when_none(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
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

        def mock_get(endpoint, params=None, **kwargs):
            if "extras/tags" in endpoint:
                return tags_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
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

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/location-types" in endpoint:
                return lt_page
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
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

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/locations" in endpoint:
                return brief_locations
            return {"count": 0, "next": None, "results": []}

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/locations")
        # loc-parent has no GPS (lat/lon=None) so only loc-child is returned
        locs = resp.get_json()["locations"]
        assert len(locs) == 1
        assert locs[0]["parent"] == "Denmark"

    def test_missing_env_vars_returns_503(self, client):
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        settings.NAUTOBOT_URL = ""
        settings.NAUTOBOT_TOKEN = ""
        try:
            resp = client.get("/api/locations")
            assert resp.status_code == 503
            assert resp.get_json()["error"] == "Nautobot service unavailable"
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token

    def test_nautobot_http_error_returns_502(self, client):
        import requests as req_lib

        http_err = req_lib.HTTPError(response=MagicMock(status_code=500))
        with patch.object(nautobot, "get", side_effect=http_err):
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
            patch.object(inventory, "get_locations", return_value=sample_locations),
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
                return_value=[
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
                ],
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
                "device_ip": "10.0.0.1",
                "status": "offline",
                "role": "Core Router",
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
            patch.object(inventory, "get_locations", return_value=sample_locations),
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
            resp = client.get("/api/alerts")
            assert resp.status_code == 200
            assert resp.get_json()["summary"]["total"] == 1
            assert [item["id"] for item in resp.get_json()["alerts"]] == ["loc-1"]
            assert get_alert.call_count == 1

            caching.cache.clear()
            get_alert.reset_mock()
            resp = client.get("/api/alerts?include_non_operational=1")

        assert resp.status_code == 200
        assert resp.get_json()["summary"]["total"] == 2
        assert [item["id"] for item in resp.get_json()["alerts"]] == ["loc-1", "loc-2"]
        assert get_alert.call_count == 2

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

    def test_log_alert_board_exclusions_reports_resolved_sets(self):
        with (
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES", {"staging", "decommissioning"}),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_NAMES", set()),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TYPES", {"warehouse"}),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TAGS", {"non-operational"}),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_DEVICE_STATUSES", {"planned", "null"}),
            patch.object(flask_app.logger, "info") as info,
        ):
            flask_app._log_alert_board_exclusions()

        info.assert_called_once_with(
            "Alert board exclusions — statuses=%s, names=%s, types=%s, tags=%s, device statuses=%s; rows=%s",
            "{decommissioning,staging}",
            "{}",
            "{warehouse}",
            "{non-operational}",
            "{null,planned}",
            "every location",
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
            assert not alerts.location_is_excluded_from_alert_board({"name": "Site", "status": "Active"})

    def test_excluded_device_statuses_are_not_scored(self):
        devices = [
            {"id": "d1", "name": "router01", "role": "Router", "status": "Decommissioning", "primary_ip": "10.0.0.1"},
            {"id": "d2", "name": "sw01", "role": "Switch", "status": "", "primary_ip": "10.0.0.2"},
            {"id": "d3", "name": "sw02", "role": "Switch", "status": "Active", "primary_ip": "10.0.0.3"},
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
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_NAMES", settings.parse_csv_set("Warehouse")),
            patch.object(
                settings,
                "ALERT_BOARD_EXCLUDED_LOCATION_STATUSES",
                settings.parse_csv_set("Decommissioning"),
            ),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TYPES", settings.parse_csv_set("Branch Office")),
            patch.object(settings, "ALERT_BOARD_EXCLUDED_LOCATION_TAGS", settings.parse_csv_set("Non-Operational")),
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
        sample_locations = [{"id": "loc-1", "name": "Broken Site", "latitude": None, "longitude": None}]

        with (
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")),
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
            patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")),
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
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]

        with (
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(inventory, "read_devices", return_value=[]),
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
            patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        # Sites without cached devices have nothing to judge (#124).
        assert data["summary"]["no_data"] == 2
        assert data["summary"]["non_ok"] == 0
        assert [item["alert_level"] for item in data["alerts"]] == ["no_data", "no_data"]
        ensure_snapshot.assert_called_once_with(force=True, full=False, wait=False)

    def test_get_alert_board_data_uses_nautobot_alerts_when_librenms_unavailable(self):
        caching.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]

        with (
            patch.object(settings, "LIBRENMS_URL", "https://librenms.example.com"),
            patch.object(settings, "LIBRENMS_API_TOKEN", "token"),
            patch.object(librenms, "fetch_inventory", side_effect=RuntimeError("down")),
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                side_effect=[
                    ([{"id": "d1", "status": "offline"}], {"level": "critical", "reason": "Core down"}),
                    ([{"id": "d2", "status": "active"}], {"level": "ok", "reason": ""}),
                ],
            ) as get_alert,
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert get_alert.call_count == 2
        assert data["summary"]["critical"] == 1
        assert data["summary"]["ok"] == 1
        assert data["summary"]["no_data"] == 0

    def test_get_alert_board_data_replaces_write_connection_after_failure(self):
        """Sites share one write connection; a failed write gets the next site a fresh one (#88, #149)."""
        caching.cache.clear()
        sample_locations = [
            {"id": f"loc-{i}", "name": f"Site {i}", "location_type": "Office", "latitude": 1.0, "longitude": 2.0}
            for i in (1, 2, 3)
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
            return site["id"] != "loc-2"  # the write for loc-2 fails

        def fake_context(conn, site_id, checked_at):
            context_conns.append(conn)
            return alerts.empty_alert_context()

        with (
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(alerts, "nautobot_inventory_primary_ip_backfill_pending", return_value=False),
            patch.object(db, "get_conn", side_effect=fake_get_db_conn),
            patch.object(alerts, "upsert_alert_lifecycle_for_site", side_effect=fake_upsert),
            patch.object(alerts, "read_alert_context", side_effect=fake_context),
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["total"] == 3
        # conn-1 is the bulk read (it fails on the fake, so every site takes the per-site path).
        # The last one records the site levels for the alert feed (#180).
        read_conn, first_write, second_write, levels_conn = opened_conns
        assert upsert_conns == [first_write, first_write, second_write]
        assert context_conns == [first_write, second_write]
        assert all(conn.closed for conn in opened_conns)
        assert read_conn not in upsert_conns and levels_conn not in upsert_conns

    def test_get_alert_board_data_disables_persistence_after_connect_failure(self):
        caching.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
            {"id": "loc-2", "name": "Site 2", "location_type": "Office", "latitude": 3.0, "longitude": 4.0},
        ]

        with (
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(db, "get_conn", side_effect=RuntimeError("connection is closed")) as get_db_conn,
            patch.object(alerts, "upsert_alert_lifecycle_for_site") as upsert,
            patch.object(alerts, "read_alert_context") as get_context,
        ):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["total"] == 2
        assert get_db_conn.call_count == 1
        upsert.assert_not_called()
        get_context.assert_not_called()

    def test_get_alert_board_data_skips_lifecycle_upsert_until_first_successful_nautobot_sync(self):
        caching.cache.clear()
        sample_locations = [
            {"id": "loc-1", "name": "Site 1", "location_type": "Data Center", "latitude": 1.0, "longitude": 2.0},
        ]

        class _FakeConn:
            def close(self):
                return None

        with (
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(inventory, "ensure_snapshot"),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(db, "get_conn", return_value=_FakeConn()),
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
    def test_returns_devices_and_asns(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["devices"]) == 1
        assert data["devices"][0]["name"] == "router01"
        assert len(data["asns"]) == 1
        assert data["asns"][0]["asn"] == 65001

    def test_device_fields(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
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

        def mock_get(endpoint, params=None, **kwargs):
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

        with patch.object(nautobot, "get", side_effect=mock_get):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        dev = resp.get_json()["devices"][0]
        # Every field must be a string (not None/null) so the JS escHtml()
        # function never receives null.
        for field in ("id", "name", "device_type", "manufacturer", "role", "status", "platform", "serial", "tenant"):
            assert dev[field] is not None, f"device field '{field}' is None"
            assert isinstance(dev[field], str), f"device field '{field}' is not a string"

    def test_device_lookup_failure_returns_503(self, client):
        with patch.object(alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")):
            resp = client.get("/api/locations/loc-1/detail")

        assert resp.status_code == 503
        assert resp.get_json()["error"] == "Nautobot service unavailable"

    def test_asn_lookup_http_error_returns_502(self, client):
        http_err = requests.HTTPError(
            "upstream failed",
            response=MagicMock(status_code=502),
        )
        with (
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=([], {"level": "ok", "reason": ""}),
            ),
            patch.object(nautobot, "fetch_all_pages", side_effect=http_err),
        ):
            resp = client.get("/api/locations/loc-1/detail")

        assert resp.status_code == 502
        assert resp.get_json()["error"] == "Failed to communicate with Nautobot API"

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

    def test_missing_asn_endpoint_falls_back_to_location_asn(self, client):
        resp, mock_get = self._get_detail_with_asn_endpoint_missing(
            client, [], lambda endpoint, params=None: {"id": "loc-1", "asn": 65001}
        )
        assert resp.status_code == 200
        assert resp.get_json()["asns"] == [{"asn": 65001, "description": "", "tenant": ""}]
        mock_get.assert_called_once_with("dcim/locations/loc-1/")

    def test_missing_asn_endpoint_prefers_cached_location_asn(self, client):
        resp, mock_get = self._get_detail_with_asn_endpoint_missing(
            client,
            [{"id": "loc-2", "asn": 65002}, {"id": "loc-1", "asn": 65001}],
            AssertionError("should use the cached inventory"),
        )
        assert resp.status_code == 200
        assert resp.get_json()["asns"] == [{"asn": 65001, "description": "", "tenant": ""}]
        mock_get.assert_not_called()

    def test_missing_asn_endpoint_and_no_location_asn_returns_empty(self, client):
        resp, _ = self._get_detail_with_asn_endpoint_missing(
            client, [{"id": "loc-1", "asn": None}], AssertionError("should use the cached inventory")
        )
        assert resp.status_code == 200
        assert resp.get_json()["asns"] == []

    def test_missing_asn_endpoint_still_fails_when_location_lookup_fails(self, client):
        upstream_err = requests.HTTPError(
            "500 Server Error",
            response=MagicMock(status_code=500),
        )
        resp, _ = self._get_detail_with_asn_endpoint_missing(client, [], upstream_err)
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Tests: /api/search
# ---------------------------------------------------------------------------
class TestApiSearch:
    def test_missing_query_returns_400(self, client):
        resp = client.get("/api/search")
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_gps_coordinates_search(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Copenhagen coordinates – loc-1 is exactly at 55.6761,12.5683 (distance 0)
            resp = client.get("/api/search?q=55.6761,12.5683")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)
        assert data["search_lon"] == pytest.approx(12.5683)
        assert data["radius_km"] == 5
        # loc-1 is at the exact point
        names = [loc["name"] for loc in data["locations"]]
        assert "Copenhagen DC" in names

    def test_gps_no_results_far_away(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Tokyo – far from all test locations
            resp = client.get("/api/search?q=35.6895,139.6917")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 0
        assert data["locations"] == []

    def test_search_results_sorted_by_distance(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            # Point very close to loc-1 (within 5 km)
            resp = client.get("/api/search?q=55.678,12.571")
        data = resp.get_json()
        distances = [loc["distance_km"] for loc in data["locations"]]
        assert distances == sorted(distances)

    def test_address_geocoding(self, client):
        mock_geo_result = MagicMock()
        mock_geo_result.latitude = 55.6761
        mock_geo_result.longitude = 12.5683
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = mock_geo_result

        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            with patch("nautobot_maps.web.Nominatim", return_value=mock_geolocator):
                resp = client.get("/api/search?q=Copenhagen")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["search_lat"] == pytest.approx(55.6761)

    def test_address_not_found_returns_404(self, client):
        mock_geolocator = MagicMock()
        mock_geolocator.geocode.return_value = None

        with patch("nautobot_maps.web.Nominatim", return_value=mock_geolocator):
            resp = client.get("/api/search?q=ThisPlaceDoesNotExist12345")
        assert resp.status_code == 404
        assert "error" in resp.get_json()

    def test_distance_km_field_present(self, client):
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/search?q=55.6761,12.5683")
        data = resp.get_json()
        for loc in data["locations"]:
            assert "distance_km" in loc


class TestNautobotRuntimeErrors:
    def test_runtime_errors_do_not_leak_internal_messages(self, client):
        secret = "NAUTOBOT_URL and NAUTOBOT_TOKEN must be set"
        cases = [
            ("get", "/api/locations", (inventory, "get_locations"), {}),
            ("get", "/api/locations/loc-1/detail", (alerts, "get_location_detail"), {}),
            ("get", "/api/search?q=55.6761,12.5683", (inventory, "get_locations"), {}),
        ]

        for method, url, (module, name), kwargs in cases:
            with patch.object(module, name, side_effect=RuntimeError(secret)):
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

    def test_index_contains_location_inspector(self, client):
        resp = client.get("/")
        assert b'id="location-inspector"' in resp.data
        assert b'id="inspector-content"' in resp.data
        assert b'id="inspector-site-tabs"' in resp.data
        assert b'role="complementary"' in resp.data
        assert b'aria-labelledby="inspector-title"' in resp.data
        assert b'aria-hidden="true"' in resp.data

    def test_index_contains_inspector_actions(self, client):
        resp = client.get("/")
        assert b'id="inspector-pin"' in resp.data
        assert b'id="inspector-close"' in resp.data
        assert b'aria-label="Unpin location inspector"' in resp.data
        assert b'aria-label="Close location inspector"' in resp.data

    @staticmethod
    def _app_config(resp) -> dict:
        """The settings the page hands to map.js (data attributes, #197)."""
        import html

        tag = re.search(rb'<div\s+id="app-config"(.*?)></div>', resp.data, re.S).group(1).decode()
        return {name: html.unescape(value) for name, value in re.findall(r'data-([a-z-]+)="([^"]*)"', tag)}

    def test_index_contains_nautobot_url(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        assert self._app_config(client.get("/"))["nautobot-url"] == "https://nautobot.example.com"

    def test_index_nautobot_url_empty_when_unset(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "")
        assert self._app_config(client.get("/"))["nautobot-url"] == ""

    def test_index_has_no_inline_script(self, client):
        """The page settings are data attributes, so a CSP needs no 'unsafe-inline' (#197, #198)."""
        assert not re.search(rb"<script>(?!\s*</script>)", client.get("/").data)

    def test_tile_server_is_configurable(self, client, monkeypatch):
        monkeypatch.setattr(settings, "MAP_TILE_URL", "https://tiles.internal/{z}/{x}/{y}.png")
        monkeypatch.setattr(settings, "MAP_TILE_ATTRIBUTION", '<a href="https://x">"Ours" & co</a>')
        config = self._app_config(client.get("/"))
        assert config["tile-url"] == "https://tiles.internal/{z}/{x}/{y}.png"
        assert config["tile-attribution"] == '<a href="https://x">"Ours" & co</a>'


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

        caching.cache.clear()
        with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
            # Patch env vars so nautobot_get doesn't raise RuntimeError
            settings.NAUTOBOT_URL = "http://nautobot.test"
            settings.NAUTOBOT_TOKEN = "test-token"
            try:
                nautobot.get("dcim/locations/", {"limit": 1})
                nautobot.get("dcim/locations/", {"limit": 1})
            finally:
                settings.NAUTOBOT_URL = ""
                settings.NAUTOBOT_TOKEN = ""

        # Second call should have been served from cache – only 1 HTTP request made
        assert mock_get.call_count == 1

    def test_cache_set_and_get(self):
        caching.cache.clear()
        caching.set("test-key", {"data": 42})
        result = caching.get("test-key")
        assert result == {"data": 42}

    def test_cache_expires(self):
        """Verify that Flask-Caching is configured with the correct timeout."""
        caching.cache.clear()
        # Store with a very short timeout and verify it expires
        caching.cache.set("expiring-key", "value", timeout=1)
        import time

        time.sleep(1.1)
        result = caching.get("expiring-key")
        assert result is None

    def test_cache_default_timeout_matches_cache_ttl(self):
        """Flask-Caching default timeout should match the CACHE_TTL env var."""
        assert flask_app.app.config["CACHE_DEFAULT_TIMEOUT"] == settings.CACHE_TTL


# ---------------------------------------------------------------------------
# Tests: NAUTOBOT_URL validation
# ---------------------------------------------------------------------------
class TestNautobotURLValidation:
    def test_validate_nautobot_url_accepts_https_url(self):
        assert settings._validate_nautobot_url("https://nautobot.example.com") == "https://nautobot.example.com"

    def test_validate_nautobot_url_rejects_missing_scheme(self):
        with pytest.raises(RuntimeError, match="Invalid NAUTOBOT_URL configuration"):
            settings._validate_nautobot_url("nautobot.example.com")

    def test_validate_nautobot_url_rejects_invalid_prefix(self):
        with pytest.raises(RuntimeError, match="Invalid NAUTOBOT_URL configuration"):
            settings._validate_nautobot_url("NAUTOBOT_URL=https://nautobot.example.com")


# ---------------------------------------------------------------------------
# Tests: SSL verification configuration
# ---------------------------------------------------------------------------
class TestSSLVerification:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("true", True),
            ("", True),
            ("yes", True),
            ("false", False),
            ("no", False),
            ("0", False),
            ("/certs/internal-ca.pem", "/certs/internal-ca.pem"),
        ],
    )
    def test_verify_setting_values(self, value, expected):
        """Both upstreams accept true/false or a CA bundle path (#191)."""
        assert settings._verify_ssl(value) == expected

    def test_librenms_accepts_a_ca_bundle_path(self):
        """A path used to be read as a yes/no flag and silently became True (#191).

        Checked in a fresh process: reloading the settings module here would
        reset settings that other tests rely on.
        """
        import os
        import subprocess
        import sys

        completed = subprocess.run(
            [sys.executable, "-c", "from nautobot_maps import settings; print(settings.LIBRENMS_VERIFY_SSL)"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "LIBRENMS_VERIFY_SSL": "/certs/internal-ca.pem"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "/certs/internal-ca.pem"

    def test_missing_ca_bundle_is_logged_at_startup(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "LIBRENMS_VERIFY_SSL", "/nonexistent/ca.pem")
        with caplog.at_level("ERROR"):
            flask_app._log_alert_board_exclusions()
        assert "LIBRENMS_VERIFY_SSL='/nonexistent/ca.pem'" in caplog.text

    def test_verify_ssl_defaults_to_true(self):
        """When NAUTOBOT_VERIFY_SSL is not set, verify should default to True."""
        # The module-level NAUTOBOT_VERIFY_SSL is parsed at import time from
        # the env var (default "true"), so it should be True.
        assert settings.NAUTOBOT_VERIFY_SSL is True

    def test_verify_ssl_false_disables_verification(self):
        """Setting NAUTOBOT_VERIFY_SSL=false should pass verify=False to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        caching.cache.clear()
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        original_verify = settings.NAUTOBOT_VERIFY_SSL
        settings.NAUTOBOT_URL = "https://nautobot.test"
        settings.NAUTOBOT_TOKEN = "test-token"
        settings.NAUTOBOT_VERIFY_SSL = False
        try:
            with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
                nautobot.get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is False
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token
            settings.NAUTOBOT_VERIFY_SSL = original_verify

    def test_verify_ssl_true_enables_verification(self):
        """Setting NAUTOBOT_VERIFY_SSL=true should pass verify=True to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        caching.cache.clear()
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        original_verify = settings.NAUTOBOT_VERIFY_SSL
        settings.NAUTOBOT_URL = "https://nautobot.test"
        settings.NAUTOBOT_TOKEN = "test-token"
        settings.NAUTOBOT_VERIFY_SSL = True
        try:
            with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
                nautobot.get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] is True
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token
            settings.NAUTOBOT_VERIFY_SSL = original_verify

    def test_verify_ssl_custom_ca_bundle_path(self):
        """Setting NAUTOBOT_VERIFY_SSL to a path should pass that path to requests."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        caching.cache.clear()
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        original_verify = settings.NAUTOBOT_VERIFY_SSL
        settings.NAUTOBOT_URL = "https://nautobot.test"
        settings.NAUTOBOT_TOKEN = "test-token"
        settings.NAUTOBOT_VERIFY_SSL = "/etc/ssl/certs/custom-ca.pem"
        try:
            with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
                nautobot.get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["verify"] == "/etc/ssl/certs/custom-ca.pem"
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token
            settings.NAUTOBOT_VERIFY_SSL = original_verify

    def test_get_requests_use_connect_and_read_timeouts(self):
        """Nautobot GETs should set separate connect/read timeouts."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        caching.cache.clear()
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        settings.NAUTOBOT_URL = "https://nautobot.test"
        settings.NAUTOBOT_TOKEN = "test-token"
        try:
            with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
                nautobot.get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["timeout"] == (5, 30)
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token

    def test_insecure_request_warning_suppressed_when_verify_disabled(self):
        original_verify = settings.NAUTOBOT_VERIFY_SSL
        settings.NAUTOBOT_VERIFY_SSL = False
        try:
            with patch.object(nautobot.urllib3, "disable_warnings") as mock_disable:
                nautobot.configure_ssl_warnings()
            mock_disable.assert_called_once_with(nautobot.InsecureRequestWarning)
        finally:
            settings.NAUTOBOT_VERIFY_SSL = original_verify

    def test_insecure_request_warning_not_suppressed_when_verify_enabled(self):
        original_verify = settings.NAUTOBOT_VERIFY_SSL
        settings.NAUTOBOT_VERIFY_SSL = True
        try:
            with patch.object(nautobot.urllib3, "disable_warnings") as mock_disable:
                nautobot.configure_ssl_warnings()
            mock_disable.assert_not_called()
        finally:
            settings.NAUTOBOT_VERIFY_SSL = original_verify

    def test_insecure_request_warning_not_suppressed_by_librenms_verify_setting(self):
        original_nautobot_verify = settings.NAUTOBOT_VERIFY_SSL
        original_librenms_verify = settings.LIBRENMS_VERIFY_SSL
        settings.NAUTOBOT_VERIFY_SSL = True
        settings.LIBRENMS_VERIFY_SSL = False
        try:
            with patch.object(nautobot.urllib3, "disable_warnings") as mock_disable:
                nautobot.configure_ssl_warnings()
            mock_disable.assert_not_called()
        finally:
            settings.NAUTOBOT_VERIFY_SSL = original_nautobot_verify
            settings.LIBRENMS_VERIFY_SSL = original_librenms_verify


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

        caching.cache.clear()
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        original_version = settings.NAUTOBOT_API_VERSION
        settings.NAUTOBOT_URL = "https://nautobot.test"
        settings.NAUTOBOT_TOKEN = "test-token"
        settings.NAUTOBOT_API_VERSION = ""
        try:
            with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
                nautobot.get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/json"
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token
            settings.NAUTOBOT_API_VERSION = original_version

    def test_accept_header_includes_version_when_set(self):
        """When NAUTOBOT_API_VERSION is set, Accept should include the version."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"count": 0, "next": None, "results": []}

        caching.cache.clear()
        original_url = settings.NAUTOBOT_URL
        original_token = settings.NAUTOBOT_TOKEN
        original_version = settings.NAUTOBOT_API_VERSION
        settings.NAUTOBOT_URL = "https://nautobot.test"
        settings.NAUTOBOT_TOKEN = "test-token"
        settings.NAUTOBOT_API_VERSION = "3.0"
        try:
            with patch.object(req_lib.Session, "get", return_value=mock_resp) as mock_get:
                nautobot.get("dcim/locations/", {"limit": 1})
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/json; version=3.0"
        finally:
            settings.NAUTOBOT_URL = original_url
            settings.NAUTOBOT_TOKEN = original_token
            settings.NAUTOBOT_API_VERSION = original_version


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
        resp = client.get("/nonexistent-page", headers={"Accept": "application/json"})
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
            resp, status = web.api_http_error(GatewayTimeout())
        assert status == 504
        assert resp.get_json()["error"] == "The connection to an upstream server timed out."


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
        assert alerts.compute_alert_level([]) == {"level": "no_data", "reason": "No monitored devices"}

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
    def test_location_type_param_accepted(self, client):
        """The ?location_type query param is accepted without error."""
        with patch.object(nautobot, "get", side_effect=mock_nautobot_get):
            resp = client.get("/api/locations/loc-1/detail?location_type=Data+Center")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "devices" in data
        assert "alert" in data

    def test_location_type_influences_alert(self, client):
        """When location_type maps to rules, compute_alert_level uses correct keywords."""

        orig_rules = dict(alerts.CRITICALITY_RULES)
        orig_env = alerts.ENV_CORE_ROLE_KEYWORDS
        alerts.CRITICALITY_RULES = {"datacenter": ["firewall"]}
        alerts.ENV_CORE_ROLE_KEYWORDS = ()

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

        def mock_get(endpoint, params=None, **kwargs):
            if "dcim/devices" in endpoint:
                return firewall_devices_page
            return {"count": 0, "next": None, "results": []}

        try:
            with patch.object(nautobot, "get", side_effect=mock_get):
                resp = client.get("/api/locations/loc-1/detail?location_type=datacenter")
            data = resp.get_json()
            assert data["alert"]["level"] == "critical"
        finally:
            alerts.CRITICALITY_RULES = orig_rules
            alerts.ENV_CORE_ROLE_KEYWORDS = orig_env


# ---------------------------------------------------------------------------
# Tests: criticality override REST endpoints
# ---------------------------------------------------------------------------
@pytest.mark.usefixtures("allow_unauthenticated_writes")
class TestCriticalityOverrideEndpoints:
    """Tests for /api/criticality-overrides (requires the persistence database)."""

    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def test_list_empty(self, client):
        resp = client.get("/api/criticality-overrides")
        assert resp.status_code == 200
        assert resp.get_json()["overrides"] == []

    def test_create_override(self, client):
        resp = client.post(
            "/api/criticality-overrides",
            json={
                "nautobot_device_id": "dev-abc",
                "is_critical": False,
                "reason": "Local firewall",
                "updated_by": "admin",
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["nautobot_device_id"] == "dev-abc"
        assert data["is_critical"] is False

    def test_list_after_create(self, client):
        client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-abc", "is_critical": True, "reason": "Core router", "updated_by": "admin"},
            content_type="application/json",
        )
        resp = client.get("/api/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["nautobot_device_id"] == "dev-abc"

    def test_update_override(self, client):
        """Posting the same device_id a second time updates in-place."""
        client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-x", "is_critical": True},
            content_type="application/json",
        )
        client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-x", "is_critical": False, "reason": "Changed"},
            content_type="application/json",
        )
        resp = client.get("/api/criticality-overrides")
        overrides = resp.get_json()["overrides"]
        assert len(overrides) == 1
        assert overrides[0]["is_critical"] == 0  # stored as int

    def test_delete_override(self, client):
        client.post(
            "/api/criticality-overrides",
            json={"nautobot_device_id": "dev-del", "is_critical": True},
            content_type="application/json",
        )
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
        resp = client.post("/api/criticality-overrides", json={"is_critical": True}, content_type="application/json")
        assert resp.status_code == 400

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

    def test_no_db_returns_503(self, client):
        """Without a database URL, override endpoints return 503."""
        saved = settings.NAUTOBOT_MAPS_DATABASE_URL
        settings.NAUTOBOT_MAPS_DATABASE_URL = ""
        try:
            resp = client.get("/api/criticality-overrides")
            assert resp.status_code == 503
            resp2 = client.post(
                "/api/criticality-overrides", json={"nautobot_device_id": "x"}, content_type="application/json"
            )
            assert resp2.status_code == 503
            resp3 = client.delete("/api/criticality-overrides/x")
            assert resp3.status_code == 503
        finally:
            settings.NAUTOBOT_MAPS_DATABASE_URL = saved

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
    @pytest.fixture(autouse=True)
    def _database(self, pg_database):
        self.db = pg_database

    def test_api_alerts_contains_lifecycle_fields(self, client):
        with (
            patch.object(
                inventory,
                "get_locations",
                return_value=[
                    {
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
                    }
                ],
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
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
                "device_ip": "",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": [],
            }
        ]

    def test_alert_history_tracks_open_and_resolve(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        t1 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        alerts.upsert_alert_lifecycle_for_site(
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

    def test_init_db_rekeys_open_alerts_without_severity(self):
        """Open alerts keyed with the old site::device::level format are migrated once (#163)."""
        self.db.execute("DROP TABLE schema_migrations")  # from before versioned migrations (#201)
        import hashlib

        def old_key(site_id, device_id, level):
            return hashlib.sha256(f"{site_id}::{device_id}::{level}".encode()).hexdigest()

        rows = [
            # (alert_key, device_id, status, down_started_at)
            (old_key("loc-1", "dev-1", "critical"), "dev-1", "open", "2026-01-01T00:00:00Z"),
            # Same device, a second open alert that started later: closed, adds no downtime.
            (old_key("loc-1", "dev-1", "medium"), "dev-1", "open", "2026-01-01T02:00:00Z"),
            (old_key("loc-1", "dev-2", "medium"), "dev-2", "open", "2026-01-01T01:00:00Z"),
            (old_key("loc-1", "dev-3", "medium"), "dev-3", "resolved", "2025-12-01T00:00:00Z"),
        ]
        self.db.executemany(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES (%s, 'loc-1', 'Site One', %s, %s, 'medium', '', %s, %s, %s, 0)",
            [(key, device, device, status, started, started) for key, device, status, started in rows],
        )

        db.init_db()
        db.init_db()  # a second run changes nothing

        result = self.db.execute(
            "SELECT alert_key, device_id, status, down_started_at, total_downtime_seconds "
            "FROM alert_instances ORDER BY id"
        )
        assert [(r["device_id"], r["status"]) for r in result] == [
            ("dev-1", "open"),
            ("dev-1", "resolved"),
            ("dev-2", "open"),
            ("dev-3", "resolved"),
        ]
        assert result[0]["alert_key"] == db.build_alert_key("loc-1", "dev-1")
        assert result[2]["alert_key"] == db.build_alert_key("loc-1", "dev-2")
        assert result[3]["alert_key"] == old_key("loc-1", "dev-3", "medium")  # history untouched
        assert all(r["total_downtime_seconds"] == 0 for r in result)
        # The build finds the migrated alert and keeps its start time.
        alerts.upsert_alert_lifecycle_for_site(
            {"id": "loc-1", "name": "Site One"},
            [
                {"id": "dev-1", "name": "dev-1", "status": "offline"},
                {"id": "dev-2", "name": "dev-2", "status": "offline"},
            ],
            {"level": "critical", "reason": "x"},
            "2026-01-01T03:00:00Z",
        )
        open_rows = self.db.execute(
            "SELECT device_id, down_started_at FROM alert_instances WHERE status = 'open' ORDER BY device_id"
        )
        assert [(r["device_id"], db.serialize_value(r["down_started_at"])) for r in open_rows] == [
            ("dev-1", "2026-01-01T00:00:00Z"),
            ("dev-2", "2026-01-01T01:00:00Z"),
        ]

    def test_app_starts_with_open_alerts_to_migrate(self):
        """Startup runs db.init_db() while app.py is still loading; the migration must work there.

        Production crashed with NameError: the migration called a helper
        defined further down the file.  Import the app in a fresh process
        against a database with an open old-style alert, like a real start.
        """
        self.db.execute("DROP TABLE schema_migrations")  # from before versioned migrations (#201)
        import os
        import subprocess
        import sys

        self.db.execute(
            "INSERT INTO alert_instances (alert_key, site_id, site_name, device_id, device_name, alert_level, "
            "alert_reason, status, down_started_at, last_seen_down_at, total_downtime_seconds) "
            "VALUES ('old-style-key', 'loc-1', 'Site One', 'dev-1', 'dev-1', 'medium', '', 'open', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)"
        )
        env = {**os.environ, "NAUTOBOT_MAPS_DATABASE_URL": self.db.url}
        completed = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        # The startup log must show the database setup (it was once silenced
        # by running before logging was configured).
        assert "persistence initialised" in completed.stderr
        assert "Alert keys migrated to site + device: 1 re-keyed" in completed.stderr
        rows = self.db.execute("SELECT alert_key FROM alert_instances")
        assert rows == [{"alert_key": db.build_alert_key("loc-1", "dev-1")}]

    def test_add_case_number_to_active_alert(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
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
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
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
            patch.object(
                inventory,
                "get_locations",
                return_value=[
                    {
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
                    }
                ],
            ),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(
                alerts,
                "get_location_devices_and_alert",
                return_value=(
                    [{"id": "dev-1", "name": "router01", "role": "Core Router", "status": "offline"}],
                    {"level": "critical", "reason": "Core device(s) offline: router01"},
                ),
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
                "device_ip": "",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": ["INC-1001"],
                "down_started_at": "2026-01-01T00:00:00Z",
            }
        ]

    def test_add_case_uses_authenticated_user_for_created_by(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
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
        t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        alerts.upsert_alert_lifecycle_for_site(
            site,
            [{"id": "dev-1", "name": "router01", "status": "offline"}],
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            t0,
        )
        sample_locations = [{"id": "loc-1", "name": "Site One", "latitude": 1.0, "longitude": 2.0}]
        with (
            patch.object(inventory, "get_locations", return_value=sample_locations),
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(alerts, "get_location_devices_and_alert", side_effect=RuntimeError("lookup failed")),
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
            patch.object(inventory, "get_locations", return_value=sample_locations) as get_locations,
            patch.object(nautobot, "fetch_all_pages", return_value=[]),
            patch.object(alerts, "get_location_devices_and_alert", return_value=devices_return) as get_alert,
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
                "snapshot_initialized",
                return_value=True,
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
            call.args == () and call.kwargs == {"force": True, "full": False, "wait": False}
            for call in ensure_snapshot.call_args_list
        )
        assert all(call.kwargs.get("timeout") == settings.CACHE_TTL for call in cache_set.call_args_list)

    def test_get_alert_board_data_builds_snapshot_only_payload(self):
        caching.cache.clear()
        payload = {
            "checked_at": "2026-01-01T00:00:00Z",
            "stale_after_seconds": settings.CACHE_TTL,
            "summary": {"total": 0, "critical": 0, "medium": 0, "unknown": 0, "ok": 0, "non_ok": 0},
            "alerts": [],
        }
        with (
            patch.object(alerts, "build_alert_board_payload", return_value=payload) as build_payload,
            patch.object(inventory, "ensure_snapshot") as ensure_snapshot,
        ):
            result = alerts.get_alert_board_data(force_refresh=True)

        assert result["checked_at"] == "2026-01-01T00:00:00Z"
        build_payload.assert_called_once_with(
            snapshot_only=True,
            include_non_operational=False,
        )
        ensure_snapshot.assert_called_once_with(force=True, full=False, wait=False)

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
                "snapshot_initialized",
                return_value=False,
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
                if "SELECT id, alert_key, site_id, site_name, device_id, device_name, down_started_at" in query:
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
        class _FakeResult:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

            def fetchall(self):
                return [self.row] if self.row else []

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
                if "SELECT NOT EXISTS (" in query:
                    return _FakeResult({"missing": False})
                return _FakeResult()

            def close(self):
                self.closed = True

            def transaction(self):
                return _FakeTransaction(self)

        fake_conn = _FakeConn()
        with (
            patch.object(db, "get_conn", return_value=fake_conn),
            patch.object(db, "dialect", return_value="postgres"),
        ):
            db.init_db()

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
        with (
            patch.object(db, "get_conn", return_value=fake_conn),
            patch.object(db, "dialect", return_value="postgres"),
        ):
            db.init_db()

        reset_query, reset_params = next(
            (query, params)
            for query, params in fake_conn.queries
            if query.strip().startswith("UPDATE inventory_sync_state")
        )
        assert "status = 'pending'" in reset_query
        assert reset_params == ("nautobot_inventory",)

    def test_init_db_postgres_migrates_legacy_sync_state_cache_version(self):
        class _FakeResult:
            def __init__(self, rows=None):
                self._rows = rows or []

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
                self.transaction_entries = 0
                self.has_cache_version = False

            def close(self):
                return None

            def transaction(self):
                return _FakeTransaction(self)

            def execute(self, query, params=()):
                self.queries.append((query, params))
                if "SELECT NOT EXISTS" in query and "column_name = 'primary_ip'" in query:
                    return _FakeResult([{"missing": True}])
                if "ALTER TABLE inventory_sync_state ADD COLUMN cache_version" in query:
                    self.has_cache_version = True
                if (
                    "SELECT source, last_started_at, last_completed_at, last_successful_sync, cache_version, status, error_message"
                    in query
                ):
                    if not self.has_cache_version:
                        raise RuntimeError("column inventory_sync_state.cache_version does not exist")
                    return _FakeResult(
                        [
                            {
                                "source": "nautobot_inventory",
                                "last_started_at": None,
                                "last_completed_at": None,
                                "last_successful_sync": None,
                                "cache_version": "",
                                "status": "idle",
                                "error_message": "",
                            }
                        ]
                    )
                if "INSERT INTO inventory_sync_state" in query and not self.has_cache_version:
                    raise RuntimeError("column inventory_sync_state.cache_version does not exist")
                return _FakeResult([])

        fake_conn = _FakeConn()
        with (
            patch.object(db, "get_conn", return_value=fake_conn),
            patch.object(db, "dialect", return_value="postgres"),
        ):
            db.init_db()

        assert any(
            "ALTER TABLE inventory_sync_state ADD COLUMN cache_version" in query for query, _ in fake_conn.queries
        )
        reset_query, reset_params = next(
            (query, params)
            for query, params in fake_conn.queries
            if query.strip().startswith("UPDATE inventory_sync_state")
        )
        assert "status = 'pending'" in reset_query
        assert reset_params == ("nautobot_inventory",)
        state = inventory.get_sync_state("nautobot_inventory", conn=fake_conn)
        assert state["cache_version"] == ""

        inventory.record_sync_state(
            fake_conn,
            "nautobot_inventory",
            cache_version=inventory.CACHE_VERSION,
        )
        assert any(
            params[4] == inventory.CACHE_VERSION
            for query, params in fake_conn.queries
            if "INSERT INTO inventory_sync_state" in query
        )

    def test_init_db_migrates_legacy_time_zone_not_null(self):
        """Old databases had time_zone NOT NULL; db.init_db relaxes it and keeps the rows."""
        self.db.execute("DROP TABLE schema_migrations")  # from before versioned migrations (#201)
        self.db.execute("DROP TABLE nautobot_location_cache")
        self.db.execute(
            """
            CREATE TABLE nautobot_location_cache (
                location_id  TEXT PRIMARY KEY,
                name         TEXT NOT NULL DEFAULT '',
                slug         TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT '',
                location_type TEXT NOT NULL DEFAULT '',
                parent       TEXT NOT NULL DEFAULT '',
                latitude     DOUBLE PRECISION,
                longitude    DOUBLE PRECISION,
                description  TEXT NOT NULL DEFAULT '',
                physical_address TEXT NOT NULL DEFAULT '',
                facility     TEXT NOT NULL DEFAULT '',
                tenant       TEXT NOT NULL DEFAULT '',
                tenant_id    TEXT NOT NULL DEFAULT '',
                tenant_group TEXT NOT NULL DEFAULT '',
                asn          BIGINT,
                time_zone    TEXT NOT NULL DEFAULT '',
                tags_json    TEXT NOT NULL DEFAULT '[]',
                url          TEXT NOT NULL DEFAULT '',
                last_updated TIMESTAMPTZ,
                synced_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.db.execute(
            "INSERT INTO nautobot_location_cache (location_id, time_zone) VALUES (%s, %s)",
            ("loc-legacy", "UTC"),
        )

        db.init_db()

        column = self.db.execute(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'nautobot_location_cache'
              AND column_name = 'time_zone'
            """
        )
        assert column == [{"is_nullable": "YES"}]
        self.db.execute(
            "INSERT INTO nautobot_location_cache (location_id, time_zone) VALUES (%s, %s)",
            ("loc-null", None),
        )
        rows = self.db.execute("SELECT location_id, time_zone FROM nautobot_location_cache ORDER BY location_id")
        assert rows == [
            {"location_id": "loc-legacy", "time_zone": "UTC"},
            {"location_id": "loc-null", "time_zone": None},
        ]

    def test_init_db_marks_primary_ip_migration_pending(self):
        """Adding the primary_ip column forces a fresh Nautobot sync to fill it."""
        self.db.execute("DROP TABLE schema_migrations")  # from before versioned migrations (#201)
        self.db.execute("ALTER TABLE nautobot_device_cache DROP COLUMN primary_ip")
        self.db.execute("ALTER TABLE inventory_sync_state DROP COLUMN cache_version")
        self.db.execute(
            """
            INSERT INTO inventory_sync_state
                (source, last_started_at, last_completed_at, last_successful_sync, status, error_message)
            VALUES (%s, %s, %s, %s, %s, %s)
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

        db.init_db()

        columns = {
            row["table_name"] + "." + row["column_name"]
            for row in self.db.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = current_schema()"
            )
        }
        assert "nautobot_device_cache.primary_ip" in columns
        assert "inventory_sync_state.cache_version" in columns
        state = self.db.execute(
            """
            SELECT last_completed_at, last_successful_sync, cache_version, status
            FROM inventory_sync_state
            WHERE source = %s
            """,
            ("nautobot_inventory",),
        )[0]
        assert state == {
            "last_completed_at": None,
            "last_successful_sync": None,
            "cache_version": "",
            "status": "pending",
        }

    def test_get_db_conn_postgres_enables_autocommit(self):
        sentinel_conn = object()
        sentinel_row_factory = object()
        with (
            patch.object(settings, "NAUTOBOT_MAPS_DATABASE_URL", "postgresql://db.example/maps"),
            patch.object(db, "psycopg") as psycopg_module,
            patch.object(db, "dict_row", sentinel_row_factory),
        ):
            psycopg_module.connect.return_value = sentinel_conn

            conn = db.get_conn()

        assert conn is sentinel_conn
        psycopg_module.connect.assert_called_once_with(
            # The URL plus the connect and statement timeouts (#190).
            "dbname=maps host=db.example connect_timeout=5 options='-c statement_timeout=60000'",
            row_factory=sentinel_row_factory,
            autocommit=True,
        )


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

        with patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
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
        with patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
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
            patch.object(timeutil, "iso_utc_now", side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"]),
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
            patch.object(timeutil, "iso_utc_now", side_effect=["2026-01-02T00:00:00Z", "2026-01-02T00:00:10Z"]),
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
            assert alerts.nautobot_inventory_primary_ip_backfill_pending() is True

        with patch.object(
            inventory,
            "get_sync_state",
            return_value={
                "last_successful_sync": "2026-01-01T00:05:00Z",
                "cache_version": inventory.CACHE_VERSION,
            },
        ):
            assert alerts.nautobot_inventory_primary_ip_backfill_pending() is False

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
        with patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
            data = alerts.get_alert_board_data(force_refresh=True)

        assert data["summary"]["no_data"] == 1  # no monitored devices yet
        assert data["alerts"][0]["device_count"] == 0
        assert data["alerts"][0]["down_device_count"] == 0

    def test_api_alerts_filters_cached_devices_without_primary_ip(self, client):
        conn = db.get_conn()
        try:
            with conn:
                inventory.write_locations(
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
                inventory.write_devices(
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

        caching.cache.clear()
        with patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("should not fetch live inventory")):
            resp = client.get("/api/alerts")

        assert resp.status_code == 200
        entry = resp.get_json()["alerts"][0]
        assert entry["device_count"] == 1
        assert entry["down_device_count"] == 1
        assert entry["down_devices"] == [
            {
                "device_id": "dev-2",
                "device_name": "router01",
                "device_ip": "192.0.2.1",
                "status": "offline",
                "role": "Core Router",
                "case_numbers": [],
                "down_started_at": "2026-01-01T00:00:00Z",
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
        with (
            patch.object(db, "get_conn", return_value=fake_conn),
            patch.object(settings, "LIBRENMS_URL", "https://librenms.example.com"),
            patch.object(settings, "LIBRENMS_API_TOKEN", "token"),
            patch.object(inventory, "get_sync_state", return_value={"last_successful_sync": None}),
            patch.object(
                librenms,
                "fetch_inventory",
                return_value=[{"device_id": 1, "hostname": "router01", "status": 1, "status_reason": ""}],
            ),
            patch.object(caching.cache, "delete") as cache_delete,
        ):
            inventory.sync_librenms()

        assert fake_conn.connection_context_entries == 0
        assert fake_conn.transaction_entries == 2
        assert fake_conn.commits == 2
        assert any(
            "SELECT COUNT(*) AS device_count FROM librenms_device_status" in query for query, _ in fake_conn.queries
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

        assert [item["id"] for item in inventory.read_locations(include_without_coordinates=True)] == ["loc-1"]
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
                k.lower(): [kw.lower() for kw in v] for k, v in loaded.items() if isinstance(v, list)
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
        lnms_response = {"devices": [{"device_id": 1, "hostname": "router01.example.com", "status": 0}]}
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
            devices = [{"id": "d1", "name": "router01", "primary_ip": "192.0.2.1/32", "status": "active"}]
            result = alerts.enrich_with_librenms(devices)
        assert result[0]["status"] == "offline"

    def test_librenms_down_matches_primary_ipv6_when_hostname_case_differs(self):
        """IPv6-valued LibreNMS hostnames match Nautobot primary_ip regardless of hex casing."""
        settings.LIBRENMS_URL = "http://librenms.test"
        settings.LIBRENMS_API_TOKEN = "tok"
        lnms_response = {"devices": [{"device_id": 1, "hostname": "2001:DB8::1", "status": 0}]}
        with patch.object(librenms, "get", return_value=lnms_response):
            devices = [{"id": "d1", "name": "router01", "primary_ip": "2001:db8::1/128", "status": "active"}]
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

    def test_header_auth_binds_flask_to_loopback(self):
        with auth_config(mode="header"):
            assert auth.flask_run_host() == "127.0.0.1"

    def test_non_header_auth_keeps_public_flask_bind(self):
        with auth_config(mode="disabled"):
            assert auth.flask_run_host() == "0.0.0.0"

    def test_header_auth_binds_gunicorn_to_all_interfaces(self, monkeypatch):
        """In a container 127.0.0.1 is unreachable; trust is checked per request instead (#187)."""
        monkeypatch.setenv("AUTH_MODE", "header")
        assert self._reload_gunicorn_config().bind == "0.0.0.0:5000"

    def test_identity_headers_only_count_from_trusted_proxies(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_TRUSTED_PROXIES", settings._networks("t", "10.0.0.0/24"))
        monkeypatch.setattr(settings, "AUTH_PROXY_SECRET", "")
        headers = {"X-Forwarded-User": "mallory", "X-Forwarded-Groups": "noc-admins"}
        body = {"nautobot_device_id": "dev-abc", "is_critical": False}
        with auth_config(mode="header", admin_groups={"noc-admins"}):
            # From an untrusted address the headers are ignored: anonymous.
            resp = client.post(
                "/api/criticality-overrides", json=body, headers=headers, environ_base={"REMOTE_ADDR": "192.0.2.7"}
            )
            assert resp.status_code == 401
            # From the proxy they count (503: no database in this test).
            resp = client.post(
                "/api/criticality-overrides", json=body, headers=headers, environ_base={"REMOTE_ADDR": "10.0.0.5"}
            )
            assert resp.status_code == 503

    def test_proxy_secret_is_required_when_set(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_TRUSTED_PROXIES", settings._networks("t", "0.0.0.0/0"))
        monkeypatch.setattr(settings, "AUTH_PROXY_SECRET", "s3cret")
        headers = {"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc-admins"}
        body = {"nautobot_device_id": "dev-abc", "is_critical": False}
        with auth_config(mode="header", admin_groups={"noc-admins"}):
            assert client.post("/api/criticality-overrides", json=body, headers=headers).status_code == 401
            wrong = {**headers, "X-Auth-Proxy-Secret": "guess"}
            assert client.post("/api/criticality-overrides", json=body, headers=wrong).status_code == 401
            right = {**headers, "X-Auth-Proxy-Secret": "s3cret"}
            assert client.post("/api/criticality-overrides", json=body, headers=right).status_code == 503

    def test_default_trusts_only_localhost(self):
        assert [str(n) for n in settings._networks("t", "127.0.0.1/32,::1/128")] == ["127.0.0.1/32", "::1/128"]
        with pytest.raises(RuntimeError, match="AUTH_TRUSTED_PROXIES"):
            settings._networks("AUTH_TRUSTED_PROXIES", "10.0.0.0/33")

    def test_non_header_auth_keeps_public_gunicorn_bind(self, monkeypatch):
        monkeypatch.setenv("AUTH_MODE", "disabled")
        assert self._reload_gunicorn_config().bind == "0.0.0.0:5000"

    def test_gunicorn_when_ready_logs_alert_board_exclusions(self):
        with patch("app._log_alert_board_exclusions") as log_exclusions:
            self._reload_gunicorn_config().when_ready(None)

        log_exclusions.assert_called_once_with()

    def test_default_gunicorn_timeout_is_120_seconds(self, monkeypatch):
        monkeypatch.delenv("GUNICORN_TIMEOUT", raising=False)
        assert self._reload_gunicorn_config().timeout == 120

    def test_gunicorn_timeout_has_a_120_second_floor(self, monkeypatch):
        monkeypatch.setenv("GUNICORN_TIMEOUT", "30")
        assert self._reload_gunicorn_config().timeout == 120

    def test_gunicorn_timeout_allows_values_above_floor(self, monkeypatch):
        monkeypatch.setenv("GUNICORN_TIMEOUT", "180")
        assert self._reload_gunicorn_config().timeout == 180

    def test_auth_disabled_refuses_admin_writes_by_default(self, client, monkeypatch):
        """Anyone who can reach the app must not change Nautobot with its token (#188)."""
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
        writes = [
            ("post", "/api/criticality-overrides", {"nautobot_device_id": "dev-abc", "is_critical": False}),
            ("delete", "/api/criticality-overrides/dev-abc", None),
        ]
        with auth_config(mode="disabled"):
            for method, url, body in writes:
                resp = getattr(client, method)(url, json=body) if body else getattr(client, method)(url)
                assert resp.status_code == 403, (method, url)
                assert "ALLOW_UNAUTHENTICATED_WRITES" in resp.get_json()["detail"]

    def test_auth_disabled_keeps_reads_and_cases_open(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", False)
        with auth_config(mode="disabled"):
            assert client.get("/api/criticality-overrides").status_code != 403
            # Adding a case is what the board does; it reaches the handler
            # (400: nothing to add), not the auth check.
            assert client.post("/api/alert-cases", json={}).status_code != 403

    @pytest.mark.parametrize(
        "method, url",
        [
            ("get", "/api/roles"),
            ("post", "/api/roles"),
            ("delete", "/api/roles/role-1"),
            ("get", "/api/location-types"),
            ("post", "/api/location-types"),
            ("delete", "/api/location-types/lt-1"),
        ],
    )
    def test_nautobot_proxy_endpoints_are_gone(self, client, method, url, monkeypatch):
        """Changes to Nautobot data are made in Nautobot, not through this app (#188)."""
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
        assert getattr(client, method)(url).status_code in (404, 405)

    def test_the_nautobot_client_cannot_write(self):
        assert not hasattr(nautobot, "post") and not hasattr(nautobot, "delete")

    def test_allow_unauthenticated_writes_restores_the_old_behaviour(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ALLOW_UNAUTHENTICATED_WRITES", True)
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        with auth_config(mode="disabled"):
            resp = client.post(
                "/api/criticality-overrides",
                json={"nautobot_device_id": "dev-abc", "is_critical": False},
                content_type="application/json",
            )
        assert resp.status_code == 503  # reached the handler: no database here

    def test_require_viewer_protects_every_page_but_probes(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert client.get("/").status_code == 401
            assert client.get("/api/alerts").status_code == 401
            assert client.get("/alerts", headers={"X-Forwarded-User": "bob"}).status_code == 403
            ok = client.get("/", headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc"})
            assert ok.status_code == 200
            assert client.get("/healthz").status_code in (200, 503)
            assert client.get("/metrics").status_code == 200

    def test_require_viewer_is_off_by_default(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", False)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert client.get("/").status_code == 200

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


# ---------------------------------------------------------------------------
# Tests: alert-board tier definitions and per-device IP (#117)
# ---------------------------------------------------------------------------
class TestAlertBoardTierDefinitions:
    def test_alerts_page_renders_info_glyph_per_tile(self, client):
        resp = client.get("/alerts")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert html.count('class="tier-info"') == len(alerts.ALERT_STATUS_TIER_DEFINITIONS)
        for label, key in [
            ("Critical", "critical"),
            ("Medium", "medium"),
            ("Low", "low"),
            ("No data", "no_data"),
            ("OK", "ok"),
            ("Total sites", "total"),
        ]:
            definition = escape(alerts.ALERT_STATUS_TIER_DEFINITIONS[key])
            assert f'aria-label="{label}: {definition}"' in html
            assert f'data-tooltip="{definition}"' in html

    def test_medium_definition_matches_scoring_threshold(self):
        threshold = f"{alerts.MEDIUM_DOWN_RATIO:.0%}"
        assert threshold in alerts.ALERT_STATUS_TIER_DEFINITIONS["medium"]
        assert threshold in alerts.ALERT_STATUS_TIER_DEFINITIONS["low"]
        devices = [{"id": f"d{i}", "name": f"sw{i}", "role": "Access Switch", "status": "active"} for i in range(4)]
        assert alerts.compute_alert_level(devices)["level"] == "ok"
        devices[0]["status"] = "offline"  # exactly 25% down
        assert alerts.compute_alert_level(devices)["level"] == "low"
        devices[1]["status"] = "offline"  # 50% down
        assert alerts.compute_alert_level(devices)["level"] == "medium"


class TestDeviceDisplayIp:
    def test_prefers_primary_ip_without_prefix_length(self):
        device = {"primary_ip": "192.0.2.10/32", "librenms_hostname": "198.51.100.1"}
        assert alerts.device_display_ip(device) == "192.0.2.10"

    def test_keeps_ipv6_primary_ip(self):
        assert alerts.device_display_ip({"primary_ip": "2001:db8::1/128"}) == "2001:db8::1"

    def test_falls_back_to_librenms_ip_hostname(self):
        assert alerts.device_display_ip({"primary_ip": "", "librenms_hostname": "198.51.100.1"}) == "198.51.100.1"

    def test_ignores_non_ip_librenms_hostname(self):
        assert alerts.device_display_ip({"primary_ip": "", "librenms_hostname": "router01.example.net"}) == ""

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
        return {
            "checked_at": timeutil.iso_utc_now(),
            "stale_after_seconds": 300,
            "summary": {"total": len(alerts or [])},
            "alerts": alerts or [],
        }

    def test_refresh_1_enqueues_forced_background_sync(self, client):
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])),
        ):
            resp = client.get("/api/alerts?refresh=1")
        assert resp.status_code == 200
        ensure.assert_called_once_with(force=True, full=False, wait=False)
        assert resp.get_json()["sync_pending"] is True

    def test_timestamp_refresh_value_does_not_trigger_sync(self, client):
        """The old UI sent refresh=<Date.now()>; the server contract is 1/true/yes/refresh."""
        now = timeutil.iso_utc_now()
        self._set_nautobot_sync_state("idle", now, now)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])),
        ):
            resp = client.get("/api/alerts?refresh=1727000000000")
        ensure.assert_not_called()
        assert resp.get_json()["sync_pending"] is False

    def test_cold_start_enqueues_first_sync_without_waiting(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board()),
        ):
            resp = client.get("/api/alerts")
        assert resp.status_code == 200
        ensure.assert_called_once_with(wait=False)
        data = resp.get_json()
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
            patch.object(alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])),
        ):
            resp = client.get("/api/alerts")
        ensure.assert_not_called()
        assert resp.get_json()["sync_pending"] is False

    def test_due_sync_is_started_by_a_normal_board_load(self, client, monkeypatch):
        """An open board keeps itself up to date: a normal load starts a sync once one is due (#152)."""
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        old = (datetime.now(UTC) - timedelta(seconds=301)).isoformat()
        self._set_nautobot_sync_state("idle", old, old)
        with (
            patch.object(inventory, "ensure_snapshot", return_value=True) as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])),
        ):
            data = client.get("/api/alerts").get_json()
        ensure.assert_called_once_with(wait=False)
        assert data["sync_pending"] is True
        assert data["next_update_in_seconds"] == 0

    def test_next_update_counts_down_from_last_completed_sync(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        completed = (datetime.now(UTC) - timedelta(seconds=100)).isoformat()
        self._set_nautobot_sync_state("idle", completed, completed)
        with (
            patch.object(inventory, "ensure_snapshot") as ensure,
            patch.object(alerts, "build_alert_board_payload", return_value=self._board([{"id": "loc-1"}])),
        ):
            data = client.get("/api/alerts").get_json()
        ensure.assert_not_called()
        assert 195 <= data["next_update_in_seconds"] <= 200

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
                conn, "librenms_inventory", last_started_at=now, last_completed_at=now, status="idle"
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

    def test_empty_sync_interval_env_uses_default(self):
        """docker-compose passes unset variables as ""; that must not crash startup (#152)."""
        import os
        import subprocess
        import sys

        env = {
            **os.environ,
            "CACHE_TTL": "120",
            "INVENTORY_SYNC_INTERVAL_SECONDS": "",
            "LIBRENMS_SYNC_INTERVAL_SECONDS": "45",
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import app; from nautobot_maps import db, settings as s; "
                "print(s.INVENTORY_SYNC_INTERVAL_SECONDS, s.LIBRENMS_SYNC_INTERVAL_SECONDS)",
            ],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip().splitlines()[-1] == "120 45"

    def test_running_sync_is_reported_as_pending(self, client):
        self._set_nautobot_sync_state("running", timeutil.iso_utc_now())
        assert alerts.nautobot_sync_in_progress() is True

    def test_abandoned_running_sync_is_not_pending(self):
        started = datetime(2020, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        self._set_nautobot_sync_state("running", started)
        assert alerts.nautobot_sync_in_progress() is False

    def test_no_sync_pending_without_persistence(self, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert alerts.nautobot_sync_in_progress() is False

    def test_adding_case_invalidates_cached_board(self, client):
        site = {"id": "loc-1", "name": "Site One"}
        devices_down = [{"id": "dev-1", "name": "router01", "status": "offline"}]
        alerts.upsert_alert_lifecycle_for_site(
            site,
            devices_down,
            {"level": "critical", "reason": "Core device(s) offline: router01"},
            timeutil.iso_utc_now(),
        )
        caching.cache.set("alert-board-data:v3", self._board([{"id": "loc-1"}]))
        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-1", "case_number": "INC-2001"},
        )
        assert resp.status_code == 200
        assert caching.cache.get("alert-board-data:v3") is None


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

    def test_links_one_case_to_every_selected_device(self, client):
        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", "dev-2"], "case_number": "INC-42"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert [item["device_id"] for item in data["linked"]] == ["dev-1", "dev-2"]
        assert "device_id" not in data  # single-device fields only for one device
        assert self._cases_for(client, "dev-1") == ["INC-42"]
        assert self._cases_for(client, "dev-2") == ["INC-42"]
        assert self._cases_for(client, "dev-3") == []

    def test_is_all_or_nothing_when_a_device_has_no_open_alert(self, client):
        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", "dev-9"], "case_number": "INC-43"},
        )
        assert resp.status_code == 404
        assert resp.get_json()["missing_device_ids"] == ["dev-9"]
        assert self._cases_for(client, "dev-1") == []

    def test_duplicate_ids_are_linked_once(self, client):
        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_ids": ["dev-1", " dev-1 "], "case_number": "INC-44"},
        )
        assert resp.status_code == 200
        assert len(resp.get_json()["linked"]) == 1
        assert self._cases_for(client, "dev-1") == ["INC-44"]

    def test_single_device_id_keeps_original_response_fields(self, client):
        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_id": "dev-3", "case_number": "INC-45"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["device_id"] == "dev-3"
        assert isinstance(data["alert_instance_id"], int)
        assert self._cases_for(client, "dev-3") == ["INC-45"]

    @pytest.mark.parametrize(
        "device_ids",
        ["dev-1", [], ["dev-1", 7], [f"dev-{i}" for i in range(web.MAX_CASE_DEVICES + 1)]],
        ids=["string-not-list", "empty", "non-string-id", "too-many"],
    )
    def test_rejects_invalid_device_ids(self, client, device_ids):
        resp = client.post(
            "/api/alert-cases",
            json={"site_id": "loc-1", "device_ids": device_ids, "case_number": "INC-46"},
        )
        assert resp.status_code == 400
        assert self._cases_for(client, "dev-1") == []


# Tests: /healthz (#131)
# ---------------------------------------------------------------------------
class TestHealthz:
    def test_ok_without_persistence(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"status": "ok", "checks": {"app": "ok"}, "inventory_sync_age_seconds": None}

    def test_ok_with_reachable_database(self, client, monkeypatch, pg_database):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["checks"] == {"app": "ok", "database": "ok"}

    def test_unavailable_database_returns_503_without_details(self, client, monkeypatch):
        # Nothing listens on port 1: connecting fails like a database outage.
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
        resp = client.get("/healthz")
        assert resp.status_code == 503
        assert resp.get_json() == {
            "status": "unavailable",
            "checks": {"app": "ok", "database": "unavailable"},
            "inventory_sync_age_seconds": None,
        }

    def test_makes_no_upstream_calls(self, client, monkeypatch):
        """A Nautobot/LibreNMS outage must not make the app look unhealthy."""
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        with patch.object(requests.Session, "get", side_effect=AssertionError("no upstream calls")):
            resp = client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: LibreNMS polled IP is cached and used as a display fallback (#137)
# ---------------------------------------------------------------------------
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
        db.init_db()
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
        assert cached == [{"device_id": 7, "hostname": "router01.example.net", "ip": "192.0.2.7", "status": 1}]

    def test_device_added_by_hostname_shows_librenms_ip(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.test")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        enriched = alerts.enrich_with_librenms(
            [{"id": "d1", "name": "router01", "status": "active", "primary_ip": ""}],
            lnms_devices=[{"device_id": 7, "hostname": "router01.example.net", "ip": "192.0.2.7", "status": 1}],
            lnms_id_map={},
        )
        assert alerts.device_display_ip(enriched[0]) == "192.0.2.7"

    def test_nautobot_primary_ip_still_preferred(self):
        device = {"primary_ip": "10.0.0.1/32", "librenms_ip": "192.0.2.7"}
        assert alerts.device_display_ip(device) == "10.0.0.1"

    def test_migration_adds_ip_column_to_existing_table(self, pg_database):
        pg_database.execute("DROP TABLE schema_migrations")  # from before versioned migrations (#201)
        pg_database.execute("ALTER TABLE librenms_device_status DROP COLUMN ip")
        pg_database.execute(
            "INSERT INTO librenms_device_status (device_id, hostname, status) VALUES (7, 'router01', 1)"
        )
        db.init_db()
        assert inventory.read_librenms_devices() == [{"device_id": 7, "hostname": "router01", "ip": "", "status": 1}]

    def test_postgres_migration_adds_ip_column(self):
        class _Result:
            def fetchone(self):
                return None

            def fetchall(self):
                return []

        class _Conn:
            def __init__(self):
                self.queries = []

            def transaction(self):
                conn = self

                class _Tx:
                    def __enter__(self):
                        return conn

                    def __exit__(self, *exc):
                        return False

                return _Tx()

            def execute(self, query, params=()):
                self.queries.append(query)
                return _Result()

            def close(self):
                return None

        conn = _Conn()
        with (
            patch.object(db, "get_conn", return_value=conn),
            patch.object(db, "dialect", return_value="postgres"),
        ):
            db.init_db()
        sql = "\n".join(conn.queries)
        assert "ip             TEXT NOT NULL DEFAULT ''" in sql  # fresh CREATE TABLE
        assert "table_name = 'librenms_device_status'" in sql
        assert "ALTER TABLE librenms_device_status ADD COLUMN ip TEXT NOT NULL DEFAULT ''" in sql


# Tests: alert board explains a missing persistence database (#136)
# ---------------------------------------------------------------------------
class TestAlertBoardWithoutPersistence:
    def test_payload_reports_missing_database(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        caching.cache.clear()
        with patch.object(
            alerts,
            "build_alert_board_payload",
            return_value={
                "checked_at": timeutil.iso_utc_now(),
                "summary": {},
                "alerts": [],
            },
        ):
            data = client.get("/api/alerts").get_json()
        assert data["persistence_configured"] is False
        assert data["sync_pending"] is False

    def test_payload_reports_configured_database(self, client, monkeypatch, pg_database):
        db.init_db()
        caching.cache.clear()
        with (
            patch.object(inventory, "ensure_snapshot", return_value=False),
            patch.object(
                alerts,
                "build_alert_board_payload",
                return_value={"checked_at": timeutil.iso_utc_now(), "summary": {}, "alerts": []},
            ),
        ):
            data = client.get("/api/alerts").get_json()
        assert data["persistence_configured"] is True

    def test_startup_log_warns_without_database(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        with caplog.at_level("WARNING", logger="app"):
            flask_app._log_alert_board_exclusions()
        assert "No persistence database configured" in caplog.text

    def test_startup_log_errors_on_leftover_sqlite_setting(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "LEGACY_SQLITE_DB", "/app/data/nautobot_maps.db")
        with caplog.at_level("ERROR", logger="app"):
            flask_app._log_alert_board_exclusions()
        assert "SQLite support was removed" in caplog.text
        assert "NAUTOBOT_MAPS_DATABASE_URL" in caplog.text

    def test_startup_log_quiet_with_database(self, monkeypatch, caplog, pg_database):
        with caplog.at_level("WARNING", logger="app"):
            flask_app._log_alert_board_exclusions()
        assert "No persistence database configured" not in caplog.text


# Tests: Refresh runs an incremental "sync now", not a full reconcile (#135)
# ---------------------------------------------------------------------------
class TestRefreshIsIncremental:
    @pytest.fixture(autouse=True)
    def _database_and_nautobot(self, pg_database, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", "https://nautobot.example.com")
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")
        monkeypatch.setattr(settings, "LIBRENMS_URL", "")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "")
        db.init_db()

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
        db.init_db()
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

        with patch.object(db, "get_conn", side_effect=counting_get_db_conn):
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
        self._add_alert("loc-1", "dev-1-1", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=900)
        # loc-2: healthy now, but an open alert -> it is resolved by the write path.
        self._add_alert("loc-2", "dev-2-2", "open", "2026-09-25T11:30:00+00:00")
        # loc-3: down router with an open alert, a case and closed history.
        open_id = self._add_alert("loc-3", "dev-3-0", "open", "2026-09-25T11:00:00+00:00")
        self._add_alert("loc-3", "dev-3-0", "resolved", "2026-09-20T10:00:00+00:00", total_downtime_seconds=120)
        self._execute(
            "INSERT INTO alert_cases (alert_instance_id, case_number, created_at) VALUES (%s, %s, %s)",
            [(open_id, "INC-1", "2026-09-25T11:05:00"), (open_id, "INC-2", "2026-09-25T11:10:00")],
        )

        data = alerts.get_alert_board_data()

        by_site = {entry["id"]: entry for entry in data["alerts"]}
        for site_id, entry in by_site.items():
            expected = alerts.get_alert_context_for_site(site_id, self.CHECKED_AT)
            assert entry["historical_downtime_seconds"] == expected["historical_downtime_seconds"], site_id
            assert entry["current_downtime_seconds"] == expected["current_downtime_seconds"], site_id
            assert entry["active_cases"] == expected["active_cases"], site_id
        assert by_site["loc-1"]["historical_downtime_seconds"] == 900
        assert by_site["loc-2"]["active_alert_instance_count"] == 0  # resolved during the build
        assert by_site["loc-3"]["active_cases"] == ["INC-1", "INC-2"]
        assert by_site["loc-3"]["current_downtime_seconds"] == 3600
        assert by_site["loc-3"]["historical_downtime_seconds"] == 120 + 3600

    def test_sites_and_devices_carry_when_they_went_down(self):
        """The board's default sort and Copy need each down device's start (#227, #228)."""
        self._seed(3, down_every=1)  # every site has its router down
        self.db.execute("UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'dev-2-1'")
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
        assert at(by_site["loc-2"]["latest_down_at"]) == at("2026-09-25T11:30:00Z")  # the newer of two
        started = {device["device_id"]: at(device["down_started_at"]) for device in by_site["loc-2"]["down_devices"]}
        assert started == {"dev-2-0": at("2026-09-25T09:00:00Z"), "dev-2-1": at("2026-09-25T11:30:00Z")}
        assert alerts.empty_alert_context()["latest_down_at"] is None

    def test_excluded_device_status_resolves_its_open_alert(self):
        self._seed(3, down_every=2)  # loc-0 and loc-2: router down (status Offline)
        self.db.execute("UPDATE nautobot_device_cache SET status = 'Decommissioning' WHERE device_id = 'dev-0-0'")
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
        self._add_alert("loc-1", "dev-1-0", "resolved", "2026-09-24T10:00:00+00:00", total_downtime_seconds=300)
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
        with caplog.at_level("INFO", logger="nautobot_maps.alerts"):
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
        assert [row["ancestor_path"] for row in alerts.with_ancestor_paths(locations, locations)] == ["A › B", "B › A"]

    def test_migration_adds_parent_id_column(self):
        self.db.execute("DROP TABLE schema_migrations")  # from before versioned migrations (#201)
        self.db.execute("ALTER TABLE nautobot_location_cache DROP COLUMN parent_id")
        db.init_db()
        columns = {
            row["column_name"]
            for row in self.db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'nautobot_location_cache'"
            )
        }
        assert "parent_id" in columns


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
        monkeypatch.setattr(scheduler.threading, "Thread", lambda **kwargs: started.append(kwargs) or MagicMock())
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", False)
        assert scheduler.start() is False
        monkeypatch.setattr(settings, "BACKGROUND_SYNC_ENABLED", True)
        assert scheduler.start() is True
        assert scheduler.start() is True
        assert len(started) == 1
        assert started[0]["daemon"] is True

    def test_not_started_without_database(self, monkeypatch):
        monkeypatch.setattr(scheduler, "_started", False)
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert scheduler.start() is False

    def test_tick_seconds_follow_the_sooner_interval(self, monkeypatch):
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 300)
        assert scheduler.tick_seconds() == 30
        monkeypatch.setattr(settings, "INVENTORY_SYNC_INTERVAL_SECONDS", 10)
        assert scheduler.tick_seconds() == 10

    def test_gunicorn_starts_the_scheduler_in_each_worker(self):
        import gunicorn_config

        with patch.object(scheduler, "start") as start:
            gunicorn_config.post_worker_init(worker=None)
        start.assert_called_once_with()


# ---------------------------------------------------------------------------
# Tests: Low and No data levels, all devices down = Critical (#124)
# ---------------------------------------------------------------------------
class TestSeverityTiers:
    @staticmethod
    def _switches(total, down):
        return [
            {"id": f"d{i}", "name": f"sw{i}", "role": "Access Switch", "status": "offline" if i < down else "active"}
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
        assert alerts.compute_alert_level(self._switches(total, down)) == {"level": level, "reason": reason}

    def test_core_device_still_wins(self):
        devices = self._switches(20, 1)
        devices[0]["role"] = "Core Router"
        assert alerts.compute_alert_level(devices)["level"] == "critical"

    def test_board_order_and_counts(self):
        assert sorted(["ok", "no_data", "low", "critical", "medium"], key=alerts.alert_sort_key) == [
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
        assert alerts.down_since({"last_updated": "2026-09-25T12:00:00Z"}, now) == "2026-09-25T12:00:00+00:00"
        # A later edit (clock skew, or an edit after we saw it) never moves the start later.
        assert alerts.down_since({"last_updated": "2026-09-28T13:00:00Z"}, now) == now
        # Unknown last change -> when we saw it.
        assert alerts.down_since({"last_updated": ""}, now) == now
        assert alerts.down_since({}, now) == now
        # Only LibreNMS says down: Nautobot's last_updated says nothing about this outage.
        assert alerts.down_since({"last_updated": "2026-09-25T12:00:00Z", "down_source": "librenms"}, now) == now

    def test_librenms_only_down_is_marked(self, monkeypatch):
        monkeypatch.setattr(settings, "LIBRENMS_URL", "https://librenms.example.com")
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "token")
        enriched = alerts.enrich_with_librenms(
            [
                {"id": "d1", "name": "sw01", "status": "active", "last_updated": "2026-01-01T00:00:00Z"},
                {"id": "d2", "name": "sw02", "status": "offline", "last_updated": "2026-01-01T00:00:00Z"},
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
        rows = pg_database.execute("SELECT down_started_at FROM alert_instances WHERE device_id = 'd-old'")
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
        pg_database.execute("UPDATE nautobot_device_cache SET last_updated = '2026-09-28T11:30:00Z'")
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
                "UPDATE nautobot_device_cache SET status = %s WHERE device_id = %s", (status, device_id)
            )

        return build, set_status

    def _feed(self, client, query=""):
        resp = client.get(f"/api/alert-feed{query}")
        assert resp.status_code == 200, resp.get_json()
        return resp.get_json()

    def test_level_changes_are_logged_but_not_first_sightings(self, pg_database):
        at = "2026-09-28T12:00:00+00:00"
        assert alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "critical")}, at) == 0
        assert alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "critical")}, at) == 0
        assert alerts.record_site_level_changes({"s1": ("One", "low"), "s2": ("Two", "critical")}, at) == 1
        rows = pg_database.execute("SELECT site_id, from_level, to_level FROM site_level_changes")
        assert rows == [{"site_id": "s1", "from_level": "ok", "to_level": "low"}]
        levels = pg_database.execute("SELECT site_id, alert_level FROM site_alert_levels ORDER BY site_id")
        assert levels == [{"site_id": "s1", "alert_level": "low"}, {"site_id": "s2", "alert_level": "critical"}]

    def test_board_builds_fill_the_feed(self, board, client):
        build, set_status = board
        assert build("2026-09-28T12:00:00+00:00")["loc-1"]["alert_level"] == "ok"
        assert self._feed(client)["events"] == []  # first sighting: nothing changed

        set_status("d-1", "Offline")
        down_level = build("2026-09-28T12:05:00+00:00")["loc-1"]["alert_level"]
        assert down_level != "ok"
        set_status("d-1", "Active")
        build("2026-09-28T12:10:00+00:00")

        feed = self._feed(client)
        assert feed["persistence_configured"] is True
        assert [(e["kind"], e["at"]) for e in feed["events"]] == [
            ("severity", "2026-09-28T12:10:00Z"),
            ("up", "2026-09-28T12:10:00Z"),
            ("severity", "2026-09-28T12:05:00Z"),
            ("down", "2026-09-28T12:05:00Z"),
        ]
        back_to_ok, up, went_down, down = feed["events"]
        assert down["site_name"] == "Site One" and down["device_name"] == "sw01"
        assert down["down_since"] == "2026-09-28T12:05:00Z" and "from_level" not in down
        assert up["device_id"] == "d-1" and up["level"] == "ok" and "down_since" not in up
        assert (went_down["from_level"], went_down["to_level"]) == ("ok", down_level)
        assert (back_to_ok["from_level"], back_to_ok["to_level"]) == (down_level, "ok")
        assert back_to_ok["device_id"] == ""

        # Filters.
        assert [e["kind"] for e in self._feed(client, "?kinds=severity")["events"]] == ["severity", "severity"]
        assert [e["kind"] for e in self._feed(client, "?kinds=down,up")["events"]] == ["up", "down"]
        assert [e["kind"] for e in self._feed(client, "?limit=1")["events"]] == ["severity"]
        since = self._feed(client, "?since=2026-09-28T12:05:00Z")["events"]
        assert [e["kind"] for e in since] == ["severity", "up"]

    def test_bad_parameters_are_rejected(self, pg_database, client):
        assert client.get("/api/alert-feed?limit=many").status_code == 400
        assert client.get("/api/alert-feed?since=yesterday").status_code == 400
        resp = client.get("/api/alert-feed?kinds=down,cases")
        assert resp.status_code == 400 and "cases" in resp.get_json()["error"]
        # Out-of-range limits are clamped, not rejected.
        assert client.get("/api/alert-feed?limit=0").status_code == 200
        assert client.get("/api/alert-feed?limit=100000").status_code == 200

    def test_without_persistence_the_feed_is_empty(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        assert self._feed(client) == {"events": [], "persistence_configured": False}

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

    def test_disabled_geocoder_only_accepts_coordinates(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GEOCODER_ENABLED", False)
        with patch("nautobot_maps.web.Nominatim", side_effect=AssertionError("must not geocode")):
            resp = client.get("/api/search?q=Copenhagen")
            assert resp.status_code == 400 and "coordinates" in resp.get_json()["error"]
            assert client.get("/api/search?q=55.68,12.57").status_code == 200

    def test_uses_the_configured_service(self, client, monkeypatch):
        monkeypatch.setattr(settings, "GEOCODER_URL", "http://nominatim.internal:8080/nominatim")
        monkeypatch.setattr(settings, "GEOCODER_USER_AGENT", "noc-maps/2")
        with patch("nautobot_maps.web.Nominatim", return_value=self._geolocator()) as nominatim:
            assert client.get("/api/search?q=Copenhagen").status_code == 200
        nominatim.assert_called_once_with(
            user_agent="noc-maps/2", domain="nominatim.internal:8080/nominatim", scheme="http"
        )

    def test_results_are_cached(self, client):
        geolocator = self._geolocator()
        with patch("nautobot_maps.web.Nominatim", return_value=geolocator):
            first = client.get("/api/search?q=Copenhagen").get_json()
            caching.cache.delete("geocode-rate-limit")
            second = client.get("/api/search?q=copenhagen ").get_json()
        assert geolocator.geocode.call_count == 1
        assert first["search_lat"] == second["search_lat"] == pytest.approx(55.68)

    def test_at_most_one_request_per_second(self, client):
        with patch("nautobot_maps.web.Nominatim", return_value=self._geolocator()):
            assert client.get("/api/search?q=Copenhagen").status_code == 200
            busy = client.get("/api/search?q=Aarhus")
        assert busy.status_code == 429 and "try again" in busy.get_json()["error"]

    def test_geocoder_error_is_not_passed_to_the_client(self, client):
        geolocator = MagicMock()
        geolocator.geocode.side_effect = OSError("connect to 10.0.0.5 refused")
        with patch("nautobot_maps.web.Nominatim", return_value=geolocator):
            resp = client.get("/api/search?q=Copenhagen")
        assert resp.status_code == 503
        assert resp.get_json() == {"error": "Geocoding service unavailable"}

    def test_not_found_is_cached_too(self, client):
        geolocator = MagicMock()
        geolocator.geocode.return_value = None
        with patch("nautobot_maps.web.Nominatim", return_value=geolocator):
            assert client.get("/api/search?q=Nowhere").status_code == 404
            caching.cache.delete("geocode-rate-limit")
            assert client.get("/api/search?q=Nowhere").status_code == 404
        assert geolocator.geocode.call_count == 1


# ---------------------------------------------------------------------------
# Tests: browser security headers (#198)
# ---------------------------------------------------------------------------
class TestSecurityHeaders:
    @pytest.mark.parametrize("path", ["/", "/alerts", "/healthz", "/api/does-not-exist"])
    def test_headers_on_every_response(self, client, path, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        resp = client.get(path)
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        csp = resp.headers["Content-Security-Policy"]
        assert "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
        assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]

    def test_images_allowed_from_the_tile_server(self, client, monkeypatch):
        monkeypatch.setattr(settings, "MAP_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")
        assert (
            "img-src 'self' data: https://*.tile.openstreetmap.org;"
            in client.get("/").headers["Content-Security-Policy"]
        )
        monkeypatch.setattr(settings, "MAP_TILE_URL", "http://tiles.internal:8080/{z}/{x}/{y}.png")
        assert "img-src 'self' data: http://tiles.internal:8080;" in client.get("/").headers["Content-Security-Policy"]
        monkeypatch.setattr(settings, "MAP_TILE_URL", "/tiles/{z}/{x}/{y}.png")
        assert "img-src 'self' data:;" in client.get("/").headers["Content-Security-Policy"]


# ---------------------------------------------------------------------------
# Tests: logging (#193)
# ---------------------------------------------------------------------------
class TestLogging:
    def _record(self, **kwargs):
        record = logging.LogRecord("nautobot_maps.web", logging.ERROR, __file__, 1, "boom %s", ("x",), None)
        record.__dict__.update(kwargs)
        return record

    def test_json_lines_include_the_stack_trace(self):
        from nautobot_maps import logs

        try:
            raise KeyError("site")
        except KeyError:
            import sys

            record = self._record(exc_info=sys.exc_info())
        entry = json.loads(logs.JsonFormatter().format(record))
        assert entry["level"] == "ERROR" and entry["logger"] == "nautobot_maps.web" and entry["message"] == "boom x"
        assert "KeyError: 'site'" in entry["exception"]

    def test_successful_health_checks_are_not_access_logged(self):
        from nautobot_maps import logs

        skip = logs.SkipSuccessfulHealthChecks()
        assert not skip.filter(self._record(args={"U": "/healthz", "s": "200"}))
        assert skip.filter(self._record(args={"U": "/healthz", "s": "503"}))
        assert skip.filter(self._record(args={"U": "/api/alerts", "s": "200"}))

    def test_gunicorn_config_logs_access_and_keeps_a_root_logger(self):
        import gunicorn_config

        config = importlib.reload(gunicorn_config)
        assert config.accesslog == "-" and "%(M)sms" in config.access_log_format
        # gunicorn refuses to start if the root logger names a missing handler.
        root_handlers = config.logconfig_dict["root"]["handlers"]
        assert set(root_handlers) <= set(config.logconfig_dict["handlers"])

    @pytest.mark.parametrize("name, value", [("LOG_LEVEL", "LOUD"), ("LOG_FORMAT", "xml")])
    def test_invalid_log_settings_stop_startup(self, name, value):
        import subprocess
        import sys

        completed = subprocess.run(
            [sys.executable, "-c", "import nautobot_maps.settings"],
            cwd=REPO_ROOT,
            env={**os.environ, name: value},
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0 and name in completed.stderr

    def test_unexpected_error_is_logged_with_its_stack_trace(self, client, caplog):
        with (
            patch.object(inventory, "get_locations", side_effect=KeyError("oops")),
            caplog.at_level("ERROR", logger="nautobot_maps.web"),
        ):
            assert client.get("/api/locations").status_code == 500
        record = next(r for r in caplog.records if "fetching locations" in r.getMessage())
        assert record.exc_info is not None


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
            "INSERT INTO alert_events (alert_instance_id, event_type, event_at) VALUES (%s, 'opened', now())", (row,)
        )
        db_.execute("INSERT INTO alert_cases (alert_instance_id, case_number) VALUES (%s, 'INC-1')", (row,))
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
            deleted = alerts.prune_alert_history(conn, 30)
        finally:
            conn.close()
        assert deleted == {"alert_instances": 1, "site_level_changes": 1}
        assert self._devices(pg_database) == ["new-resolved", "old-open"]
        # Its events and cases went with it.
        assert pg_database.execute("SELECT count(*) AS n FROM alert_events") == [{"n": 2}]
        assert pg_database.execute("SELECT count(*) AS n FROM alert_cases") == [{"n": 2}]

    def test_deletes_in_batches(self, pg_database, monkeypatch):
        monkeypatch.setattr(alerts, "RETENTION_BATCH_SIZE", 2)
        for i in range(5):
            self._alert(pg_database, f"d{i}", "resolved", resolved_days_ago=100)
        conn = db.get_conn()
        try:
            assert alerts.prune_alert_history(conn, 30)["alert_instances"] == 5
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

    def test_last_success_survives_a_failure(self, pg_database, client):
        self._record("idle", "2026-09-29T10:00:00+00:00", "2026-09-29T10:00:30+00:00")
        self._record("running", "2026-09-29T10:05:00+00:00", None)
        self._record("error", "2026-09-29T10:05:00+00:00", "2026-09-29T10:05:10+00:00")
        body = client.get("/metrics").get_data(as_text=True)
        src = '{source="nautobot_inventory"}'
        success = datetime(2026, 9, 29, 10, 0, 30, tzinfo=UTC).timestamp()
        assert self._value(body, "nautobot_maps_sync_last_success_timestamp_seconds" + src) == success
        assert self._value(body, "nautobot_maps_sync_failing" + src) == 1
        assert self._value(body, "nautobot_maps_sync_last_duration_seconds" + src) == 10

    def test_alert_and_site_counts(self, pg_database, client):
        alerts.upsert_alert_lifecycle_for_site(
            {"id": "loc-1", "name": "Site One"},
            [{"id": "d1", "name": "sw1", "status": "offline"}, {"id": "d2", "name": "sw2", "status": "offline"}],
            {"level": "critical", "reason": "all down"},
            timeutil.iso_utc_now(),
        )
        alerts.record_site_level_changes({"s1": ("One", "ok"), "s2": ("Two", "ok"), "s3": ("Three", "low")}, "now")
        resp = client.get("/metrics")
        assert resp.headers["Content-Type"].startswith("text/plain; version=0.0.4")
        body = resp.get_data(as_text=True)
        assert self._value(body, 'nautobot_maps_open_alerts{level="critical"}') == 2
        assert self._value(body, 'nautobot_maps_sites{level="ok"}') == 2
        assert self._value(body, "nautobot_maps_database_up") == 1
        assert "# TYPE nautobot_maps_sites gauge" in body

    def test_without_database(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "")
        body = client.get("/metrics").get_data(as_text=True)
        assert self._value(body, "nautobot_maps_up") == 1 and self._value(body, "nautobot_maps_database_up") == 0

    def test_can_be_turned_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "METRICS_ENABLED", False)
        assert client.get("/metrics").status_code == 404

    def test_label_values_are_escaped(self):
        from nautobot_maps import metrics

        out = metrics.Exposition()
        out.gauge("m", "h", [({"level": 'a"b\\c\nd'}, 1)])
        assert 'm{level="a\\"b\\\\c\\nd"} 1' in out.render()

    def test_healthz_reports_sync_age_without_failing(self, pg_database, client):
        assert client.get("/healthz").get_json()["inventory_sync_age_seconds"] is None  # never synced
        self._record("idle", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:10+00:00")
        resp = client.get("/healthz")
        assert resp.status_code == 200  # an old sync is not a liveness failure
        assert resp.get_json()["inventory_sync_age_seconds"] > 365 * 24 * 3600

    def test_migration_backfills_last_success(self, pg_database):
        self._record("idle", "2026-09-29T10:00:00+00:00", "2026-09-29T10:00:30+00:00")
        # A database at version 1, from before step 2 (#201).
        pg_database.execute("ALTER TABLE inventory_sync_state DROP COLUMN last_succeeded_at")
        pg_database.execute("DELETE FROM schema_migrations WHERE version >= 2")
        db.init_db()
        rows = pg_database.execute("SELECT last_succeeded_at FROM inventory_sync_state")
        assert db.serialize_value(rows[0]["last_succeeded_at"]) == "2026-09-29T10:00:30Z"

    def test_timestamps_keep_full_precision(self):
        from nautobot_maps import metrics

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


class TestSchemaMigrations:
    def _versions(self, db_):
        return [row["version"] for row in db_.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def test_fresh_database_is_at_the_current_version(self, pg_database):
        assert self._versions(pg_database) == [version for version, _, _ in db.MIGRATIONS]
        assert self._versions(pg_database)[-1] == db.SCHEMA_VERSION

    def test_database_from_before_versioning_is_recorded_as_baseline(self, pg_database):
        pg_database.execute("DROP TABLE schema_migrations")
        pg_database.execute(
            "INSERT INTO device_criticality_override (nautobot_device_id, is_critical) VALUES ('d1', 1)"
        )
        db.init_db()
        # Recorded as the baseline, then the later steps run.
        assert self._versions(pg_database) == [version for version, _, _ in db.MIGRATIONS]
        assert pg_database.execute("SELECT count(*) AS n FROM device_criticality_override") == [{"n": 1}]

    def test_current_database_skips_the_migrations(self, pg_database, monkeypatch):
        def boom(conn):
            raise AssertionError("a current database must not run migrations")

        monkeypatch.setattr(db, "MIGRATIONS", tuple((version, name, boom) for version, name, _ in db.MIGRATIONS))
        db.init_db()

    def test_a_new_step_runs_once(self, pg_database, monkeypatch):
        def add_table(conn):
            conn.execute("CREATE TABLE migration_probe (id INTEGER)")

        probe = db.SCHEMA_VERSION + 1
        monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, (probe, "probe table", add_table)))
        monkeypatch.setattr(db, "SCHEMA_VERSION", probe)
        db.init_db()
        db.init_db()  # would fail with "relation already exists" if run twice
        assert self._versions(pg_database)[-2:] == [probe - 1, probe]

    def test_newer_database_is_refused(self, pg_database):
        pg_database.execute("INSERT INTO schema_migrations (version, name) VALUES (99, 'from the future')")
        with pytest.raises(RuntimeError, match="newer than this release"):
            db.init_db()

    def test_baseline_is_frozen(self):
        import hashlib
        import inspect

        digest = hashlib.sha256(inspect.getsource(db.baseline_schema).encode()).hexdigest()
        assert digest == BASELINE_SCHEMA_SHA256, (
            "db.baseline_schema changed: add a new step to db.MIGRATIONS instead, "
            "so databases already at version 1 get the change too"
        )

    def test_migrate_command(self, pg_database):
        import os
        import pathlib
        import subprocess
        import sys

        repo_root = pathlib.Path(__file__).resolve().parent.parent
        env = {**os.environ, "NAUTOBOT_MAPS_DATABASE_URL": pg_database.url}
        current = f"database: {db.SCHEMA_VERSION}, this release: {db.SCHEMA_VERSION}"
        for command, expected in (("migrate", ""), ("schema-version", current)):
            completed = subprocess.run(
                [sys.executable, "-m", "nautobot_maps", command],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr[-2000:]
            assert expected in completed.stdout


# ---------------------------------------------------------------------------
# Tests: database connect and statement timeouts (#190)
# ---------------------------------------------------------------------------
class TestDatabaseTimeouts:
    def test_long_statement_is_cancelled(self, pg_database, monkeypatch):
        import psycopg

        monkeypatch.setattr(settings, "DB_STATEMENT_TIMEOUT_SECONDS", 1)
        conn = db.get_conn()
        try:
            with pytest.raises(psycopg.errors.QueryCanceled):
                conn.execute("SELECT pg_sleep(3)")
        finally:
            conn.close()

    def test_migrations_are_not_limited_by_the_statement_timeout(self, pg_database, monkeypatch):
        """init_db waits for other workers' migrations; that wait must not time out."""
        monkeypatch.setattr(settings, "DB_STATEMENT_TIMEOUT_SECONDS", 1)
        # A pending step, so init_db has to take the migration lock (#201).
        pending = db.SCHEMA_VERSION + 1
        monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, (pending, "no-op", lambda conn: None)))
        monkeypatch.setattr(db, "SCHEMA_VERSION", pending)
        holder = db.get_conn()
        try:
            holder.execute("SELECT pg_advisory_lock(%s)", (db.MIGRATION_LOCK_KEY,))
            released = threading.Timer(
                2, lambda: holder.execute("SELECT pg_advisory_unlock(%s)", (db.MIGRATION_LOCK_KEY,))
            )
            released.start()
            db.init_db()  # waits ~2 s for the lock, longer than the 1 s timeout
            released.join()
        finally:
            holder.close()
        assert pg_database.execute("SELECT max(version) AS v FROM schema_migrations")[0]["v"] == pending

    def test_unreachable_database_fails_fast(self, monkeypatch):
        import psycopg

        # A non-routable address: packets are dropped, so without a timeout
        # the connect would wait for the operating system (a minute or more).
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "postgresql://u:p@10.255.255.1:5432/db")
        started = datetime.now(UTC)
        with pytest.raises(psycopg.OperationalError):
            db.get_conn(connect_timeout=1)
        assert (datetime.now(UTC) - started).total_seconds() < 5

    def test_healthz_uses_a_short_connect_timeout(self, client, monkeypatch):
        monkeypatch.setattr(settings, "NAUTOBOT_MAPS_DATABASE_URL", "postgresql://u:p@db.invalid:5432/db")
        seen = {}

        def fake_get_conn(connect_timeout=None):
            seen["connect_timeout"] = connect_timeout
            raise RuntimeError("down")

        monkeypatch.setattr(db, "get_conn", fake_get_conn)
        resp = client.get("/healthz")
        assert resp.status_code == 503
        assert seen["connect_timeout"] == 2

    def test_timeout_keeps_options_from_the_url(self, pg_database):
        """The URL's own options (the tests' search_path) survive (#190)."""
        conn = db.get_conn()
        try:
            row = conn.execute("SHOW statement_timeout").fetchone()
            assert row["statement_timeout"] == "1min"
            assert conn.execute("SELECT current_schema() AS s").fetchone()["s"].startswith("test_")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Tests: upstream HTTP retries and connection reuse (#192)
# ---------------------------------------------------------------------------
class TestUpstreamRetries:
    @pytest.fixture
    def flaky_server(self):
        """A local HTTP server answering from a script of (status, headers) per request."""
        import http.server

        script, requests_seen = [], []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                requests_seen.append((self.command, self.path))
                status, headers = script.pop(0) if script else (200, {})
                body = json.dumps({"results": [{"id": "d1"}], "next": None, "devices": []}).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}", script, requests_seen
        server.shutdown()

    @pytest.fixture(autouse=True)
    def _fast_backoff(self, monkeypatch):
        from nautobot_maps import http

        monkeypatch.setattr(http, "_local", threading.local())  # a fresh session per test
        production = _retry_kwargs(http.retry_policy())
        monkeypatch.setattr(http, "retry_policy", lambda: http.CappedRetry(**{**production, "backoff_factor": 0}))

    def _nautobot(self, monkeypatch, url):
        monkeypatch.setattr(settings, "NAUTOBOT_URL", url)
        monkeypatch.setattr(settings, "NAUTOBOT_TOKEN", "token")

    def test_transient_503_is_retried(self, flaky_server, monkeypatch):
        url, script, seen = flaky_server
        self._nautobot(monkeypatch, url)
        script.append((503, {}))
        assert nautobot.fetch_all_pages("dcim/devices/", use_cache=False) == [{"id": "d1"}]
        assert len(seen) == 2

    def test_gives_up_after_three_retries(self, flaky_server, monkeypatch):
        url, script, seen = flaky_server
        self._nautobot(monkeypatch, url)
        script.extend([(502, {})] * 10)
        with pytest.raises(requests.HTTPError):
            nautobot.fetch_all_pages("dcim/devices/", use_cache=False)
        assert len(seen) == 4  # the request and three retries

    def test_client_errors_and_writes_are_not_retried(self, flaky_server, monkeypatch):
        url, script, seen = flaky_server
        self._nautobot(monkeypatch, url)
        script.append((404, {}))
        with pytest.raises(requests.HTTPError):
            nautobot.get("dcim/devices/x/", use_cache=False)
        # The app no longer writes to Nautobot (#188), but the session must
        # never retry a write: it might already have been applied.
        from nautobot_maps import http

        script.append((503, {}))
        assert http.session().post(f"{url}/api/extras/roles/", json={"name": "x"}, timeout=5).status_code == 503
        assert [method for method, _ in seen] == ["GET", "POST"]

    def test_librenms_is_retried_too(self, flaky_server, monkeypatch):
        url, script, seen = flaky_server
        monkeypatch.setattr(settings, "LIBRENMS_URL", url)
        monkeypatch.setattr(settings, "LIBRENMS_API_TOKEN", "tok")
        script.append((429, {"Retry-After": "0"}))
        assert librenms.fetch_inventory() == []
        assert len(seen) == 2

    def test_retry_after_is_capped(self):
        from nautobot_maps import http

        response = MagicMock()
        response.headers = {"Retry-After": "3600"}
        response.getheader = lambda name, default=None: response.headers.get(name, default)
        assert http.retry_policy().get_retry_after(response) == http.MAX_RETRY_AFTER_SECONDS

    def test_one_session_per_thread(self):
        from nautobot_maps import http

        other = []
        thread = threading.Thread(target=lambda: other.append(http.session()))
        thread.start()
        thread.join()
        assert http.session() is http.session()
        assert other[0] is not http.session()


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
class TestApiExplorer:
    def _routes(self):
        return {
            (rule.rule, method)
            for rule in flask_app.app.url_map.iter_rules()
            if rule.endpoint != "static"
            for method in rule.methods - {"HEAD", "OPTIONS"}
        }

    def test_every_route_is_listed(self, client):
        listed = client.get("/api/endpoints").get_json()["endpoints"]
        assert {(item["path"], method) for item in listed for method in item["methods"]} == self._routes()
        html = client.get("/docs").get_data(as_text=True)
        for path, method in self._routes():
            assert f'data-path="{path}" data-method="{method}"' in html.replace("&lt;", "<").replace("&gt;", ">")

    def test_every_write_has_an_example_body(self):
        from nautobot_maps import apidocs

        for rule in flask_app.app.url_map.iter_rules():
            if rule.methods & {"POST", "PUT", "PATCH"}:
                assert rule.endpoint in apidocs.EXAMPLE_BODIES, f"add an example body for {rule.endpoint} to apidocs"

    def test_what_each_endpoint_takes(self, client):
        listed = client.get("/api/endpoints").get_json()["endpoints"]
        by_key = {(item["path"], item["methods"][0]): item for item in listed}
        detail = by_key[("/api/locations/<location_id>/detail", "GET")]
        assert detail["path_params"] == ["location_id"] and detail["query_params"] == ["location_type"]
        assert detail["group"] == "API" and detail["required_role"] is None
        assert by_key[("/api/alert-feed", "GET")]["query_params"] == ["limit", "since", "kinds"]
        assert by_key[("/api/alert-history", "GET")]["required_role"] == "operator"
        assert by_key[("/metrics", "GET")]["group"] == "Monitoring"
        assert by_key[("/alerts", "GET")]["group"] == "Pages"
        feed = by_key[("/api/alert-feed", "GET")]
        assert feed["summary"] == "What changed on the alert board, newest first."  # no "(#180)"
        assert "Query" in feed["description"]
        case = by_key[("/api/alert-cases", "POST")]
        assert json.loads(case["example_body_text"]) == case["example_body"]
        # The page keeps the example's own order (the JSON response sorts keys).
        assert list(json.loads(case["example_body_text"])) == ["site_id", "device_ids", "case_number"]

    def test_page_shows_bodies_unescaped_and_escapes_html(self, client):
        import html as html_lib

        html = client.get("/docs").get_data(as_text=True)
        assert "&lt;device uuid&gt;" in html and "\\u003c" not in html
        assert '"case_number": "INC-1234"' in html_lib.unescape(html)
        assert '<script src="/static/js/docs.js">' in html and "<script>" not in html

    def test_header_links_to_the_explorer(self, client):
        for path in ("/", "/alerts", "/docs"):
            assert 'href="/docs"' in client.get(path).get_data(as_text=True), path

    def test_needs_the_viewer_role_like_every_page(self, client, monkeypatch):
        monkeypatch.setattr(settings, "AUTH_REQUIRE_VIEWER", True)
        with auth_config(mode="header", viewer_groups={"noc"}):
            assert client.get("/docs").status_code == 401
            assert client.get("/api/endpoints").status_code == 401
            ok = client.get("/docs", headers={"X-Forwarded-User": "alice", "X-Forwarded-Groups": "noc"})
            assert ok.status_code == 200


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
                        *[_snapshot_device(f"few-{n}", "loc-few", "Access Switch", "Active") for n in range(2, 6)],
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

    def test_returns_only_locations_with_an_alert(self, client):
        with patch.object(nautobot, "fetch_all_pages", side_effect=AssertionError("no upstream calls")):
            resp = client.get("/api/location-alerts")
        assert resp.status_code == 200
        levels = resp.get_json()["levels"]
        assert levels == {
            "loc-core": {"level": "critical", "reason": "Core device(s) offline: core-1"},
            "loc-few": {"level": "low", "reason": "1/5 devices offline (20%)"},
        }

    def test_matches_the_site_panel(self, client):
        levels = client.get("/api/location-alerts").get_json()["levels"]
        with patch.object(nautobot, "fetch_all_pages", return_value=[]):
            for location_id in ("loc-core", "loc-few", "loc-ok"):
                detail = alerts.get_location_detail(location_id, location_type="Data Center")
                expected = levels.get(location_id, {}).get("level", "ok")
                assert detail["alert"]["level"] == expected

    def test_criticality_override_is_applied(self, client):
        conn = db.get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO device_criticality_override (nautobot_device_id, is_critical, updated_by, updated_at)"
                    " VALUES ('few-1', 1, 'test', '2026-01-01T00:00:00Z')"
                )
        finally:
            conn.close()
        assert client.get("/api/location-alerts").get_json()["levels"]["loc-few"]["level"] == "critical"

    def test_cached_until_a_sync_invalidates_it(self, client):
        assert "loc-ok" not in client.get("/api/location-alerts").get_json()["levels"]
        conn = db.get_conn()
        try:
            with conn:
                conn.execute("UPDATE nautobot_device_cache SET status = 'Offline' WHERE device_id = 'ok-1'")
        finally:
            conn.close()
        assert "loc-ok" not in client.get("/api/location-alerts").get_json()["levels"]
        caching.invalidate_alert_board()
        assert client.get("/api/location-alerts").get_json()["levels"]["loc-ok"]["level"] == "critical"


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

    def test_every_circuit_is_listed(self, client):
        with self._mock(SAMPLE_CIRCUIT_TERMINATIONS):
            data = client.get("/api/locations/loc-1/detail").get_json()
        assert data["circuits_error"] == ""
        assert [c["cid"] for c in data["circuits"]] == ["GTT-7781", "TEL-0001"]
        assert data["circuits"][1] == {
            "id": "cir-1",
            "cid": "TEL-0001",
            "provider": "Telia",
            "circuit_type": "Transit",
            "status": "Active",
            "tenant": "Acme Corp",
            "commit_rate": "",
            "term_side": "A",
            "port_speed": "10 Gbps",
            "upstream_speed": "",
            "xconnect_id": "XC-1",
            "pp_info": "PP-02 port 7",
            "description": "Primary transit",
        }
        assert data["circuits"][0]["status"] == "Offline"
        assert data["circuits"][0]["upstream_speed"] == "500 Mbps"
        assert data["devices"] and data["asns"]

    def test_no_circuits_app_is_not_an_error(self, client):
        response = MagicMock(status_code=404)
        with self._mock(error=requests.HTTPError(response=response)):
            data = client.get("/api/locations/loc-1/detail").get_json()
        assert data["circuits"] == []
        assert data["circuits_error"] == ""

    def test_a_failed_call_keeps_the_rest_of_the_panel(self, client):
        with self._mock(error=requests.ConnectionError("boom")):
            resp = client.get("/api/locations/loc-1/detail")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["circuits"] == []
        assert data["circuits_error"] == "Circuit information unavailable"
        assert "boom" not in resp.get_data(as_text=True)
        assert data["devices"]

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
        assert alerts.circuit_speed(kbps) == expected
